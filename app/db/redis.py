"""
Connexion Redis — Cache multi-niveaux, sessions et rate limiting.

Namespaces Redis utilisés :
    cache:sql:{hash}        → Résultats SQL (DataFrames)
    cache:plan:{hash}       → Plans d'exécution
    cache:response:{hash}   → Réponses complètes
    flag:{key}              → Flags de confiance (soft invalidate)
    session:{session_id}    → Sessions conversationnelles
    rate:{user_id}:{window} → Compteurs rate limiting
    stats:*                 → Statistiques et métriques
    queue:*                 → Réservé pour Celery (Sprint 2/3)

Convention de clés :
    - Les méthodes publiques acceptent toujours la donnée BRUTE
      (question, semantic_context, etc.).
    - Le hash est calculé UNE SEULE FOIS à l'intérieur de chaque méthode.
    - Les flags utilisent la CLÉ REDIS COMPLÈTE (prefix + hash)
      pour garantir la correspondance exacte.
"""

import hashlib
import json
import logging
import time
from typing import Any

import redis.asyncio as aioredis

from app.config import settings

logger = logging.getLogger(__name__)


class RedisClient:
    """
    Client Redis async avec cache multi-niveaux,
    gestion de sessions, rate limiting et métriques.
    """

    # ─── Préfixes de namespaces ───────────────────────────────
    PREFIX_SQL = "cache:sql:"
    PREFIX_PLAN = "cache:plan:"
    PREFIX_RESPONSE = "cache:response:"
    PREFIX_FLAG = "flag:"
    PREFIX_SESSION = "session:"
    PREFIX_RATE = "rate:"
    PREFIX_STATS = "stats:"
    PREFIX_QUEUE = "queue:"

    def __init__(self):
        self._client: aioredis.Redis = aioredis.from_url(
            settings.redis_url,
            decode_responses=True,
        )

    # ─── Connexion ────────────────────────────────────────────

    async def ping(self) -> bool:
        return await self._client.ping()

    async def is_healthy(self) -> bool:
        """Healthcheck Redis."""
        try:
            return await self._client.ping()
        except Exception:
            return False

    async def close(self) -> None:
        await self._client.close()

    @property
    def client(self) -> aioredis.Redis:
        return self._client

    # ─── Utilitaires ──────────────────────────────────────────

    @staticmethod
    def _hash_key(data: str) -> str:
        """Génère un hash SHA-256 court pour servir de clé cache."""
        return hashlib.sha256(data.encode()).hexdigest()[:16]

    @staticmethod
    def _serialize(value: Any) -> str:
        """Sérialise une valeur Python en JSON."""
        return json.dumps(value, ensure_ascii=False, default=str)

    @staticmethod
    def _deserialize(value: str) -> Any:
        """Désérialise une chaîne JSON en objet Python."""
        return json.loads(value)

    async def _scan_keys(self, pattern: str) -> list[str]:
        """
        Parcourt toutes les clés Redis correspondant au pattern.
        Méthode privée réutilisable — résout le bug de boucle SCAN
        en utilisant correctement le curseur (commence à 0 int,
        et boucle tant que le curseur retourné n'est pas 0).
        """
        keys: list[str] = []
        cursor = 0
        while True:
            cursor, batch = await self._client.scan(
                cursor=cursor, match=pattern, count=100
            )
            keys.extend(batch)
            if cursor == 0:
                break
        return keys

    def _build_cache_key(self, prefix: str, raw_data: str) -> str:
        """
        Construit une clé Redis complète à partir du prefix
        et de la donnée brute. Le hash est appliqué ici UNE SEULE FOIS.
        """
        return prefix + self._hash_key(raw_data)

    # ==========================================================
    # 1. CACHE DES RÉSULTATS SQL
    # ==========================================================

    async def set_sql_cache(
        self, semantic_context: str, result: Any, is_historical: bool = False
    ) -> None:
        """
        Stocke un résultat SQL dans le cache.

        Args:
            semantic_context: Le SemanticContext JSON brut.
            result: Le résultat à cacher (DataFrame sérialisé, dict, etc.).
            is_historical: True = TTL 24h, False = TTL 1h.
        """
        key = self._build_cache_key(self.PREFIX_SQL, semantic_context)
        ttl = settings.cache_ttl_historical if is_historical else settings.cache_ttl_transactional
        payload = self._serialize({
            "data": result,
            "cached_at": time.time(),
            "is_historical": is_historical,
        })
        await self._client.setex(key, ttl, payload)
        await self._increment_stat("cache:sql:writes")
        logger.debug("Cache SQL set : %s (TTL=%ds)", key, ttl)

    async def get_sql_cache(self, semantic_context: str) -> Any | None:
        """
        Récupère un résultat SQL depuis le cache.
        Vérifie le flag de confiance avant de retourner.
        Retourne None si absent ou flaggé deprecated.
        """
        key = self._build_cache_key(self.PREFIX_SQL, semantic_context)

        if await self._is_deprecated(key):
            await self._increment_stat("cache:sql:deprecated_hits")
            logger.debug("Cache SQL ignoré (deprecated) : %s", key)
            return None

        raw = await self._client.get(key)
        if raw is None:
            await self._increment_stat("cache:sql:misses")
            return None

        await self._increment_stat("cache:sql:hits")
        return self._deserialize(raw)["data"]

    # ==========================================================
    # 2. CACHE DES PLANS D'EXÉCUTION
    # ==========================================================

    async def set_execution_plan(self, question: str, plan: dict) -> None:
        """
        Stocke un plan d'exécution généré par l'Orchestrateur.

        Args:
            question: La question brute de l'utilisateur.
            plan: Le plan d'exécution (séquence d'agents).
        """
        key = self._build_cache_key(self.PREFIX_PLAN, question)
        payload = self._serialize({
            "plan": plan,
            "cached_at": time.time(),
        })
        await self._client.setex(key, settings.cache_ttl_execution_plan, payload)
        await self._increment_stat("cache:plan:writes")
        logger.debug("Cache plan set : %s", key)

    async def get_execution_plan(self, question: str) -> dict | None:
        """
        Récupère un plan d'exécution depuis le cache.

        Args:
            question: La question brute de l'utilisateur.
        """
        key = self._build_cache_key(self.PREFIX_PLAN, question)

        if await self._is_deprecated(key):
            return None

        raw = await self._client.get(key)
        if raw is None:
            await self._increment_stat("cache:plan:misses")
            return None

        await self._increment_stat("cache:plan:hits")
        return self._deserialize(raw)["plan"]

    # ==========================================================
    # 3. CACHE DES RÉPONSES COMPLÈTES
    # ==========================================================

    async def set_response_cache(
        self,
        question: str,
        response: dict,
        is_historical: bool | int = False,
        ttl_s: int | None = None,
    ) -> None:
        """
        Stocke la réponse complète (texte + graphiques + insights).

        Args:
            question: La question brute de l'utilisateur.
            response: La réponse complète à cacher.
            is_historical: True = TTL 24h, False = TTL 1h.
        """
        key = self._build_cache_key(self.PREFIX_RESPONSE, question)
        if ttl_s is not None:
            ttl = ttl_s
            historical = False
        elif type(is_historical) is int:
            ttl = is_historical
            historical = False
        else:
            historical = bool(is_historical)
            ttl = (
                settings.cache_ttl_historical
                if historical
                else settings.cache_ttl_transactional
            )
        payload = self._serialize({
            "response": response,
            "original_question": question,
            "cached_at": time.time(),
            "is_historical": historical,
        })
        await self._client.setex(key, ttl, payload)
        await self._increment_stat("cache:response:writes")
        logger.debug("Cache réponse set : %s (TTL=%ds)", key, ttl)

    async def get_response_cache(self, question: str) -> dict | None:
        """
        Récupère une réponse complète depuis le cache.
        Vérifie le flag de confiance avant de retourner.

        Args:
            question: La question brute de l'utilisateur.
        """
        key = self._build_cache_key(self.PREFIX_RESPONSE, question)

        if await self._is_deprecated(key):
            await self._increment_stat("cache:response:deprecated_hits")
            return None

        raw = await self._client.get(key)
        if raw is None:
            await self._increment_stat("cache:response:misses")
            return None

        await self._increment_stat("cache:response:hits")
        return self._deserialize(raw)["response"]

    # ==========================================================
    # 4. FLAG DE CONFIANCE (SOFT INVALIDATE)
    # ==========================================================
    #
    # Convention : on flag toujours avec la CLÉ REDIS COMPLÈTE
    # (ex: "cache:sql:a1b2c3d4e5f6g7h8"). Cela garantit une
    # correspondance exacte entre le cache et son flag.
    #

    async def flag_deprecated(self, cache_key: str, reason: str = "") -> None:
        """
        Marque une entrée cache comme deprecated sans la supprimer.
        Le Feedback Agent utilise cette méthode quand un résultat
        est jugé mauvais.

        L'entrée cache originale reste intacte pour audit/comparaison.
        L'Orchestrateur vérifiera ce flag avant de servir le cache.

        Args:
            cache_key: La clé Redis COMPLÈTE à flagger
                       (ex: "cache:sql:a1b2c3d4e5f6g7h8").
            reason: Raison du flag (optionnel).
        """
        flag_key = self.PREFIX_FLAG + cache_key
        payload = self._serialize({
            "deprecated": True,
            "reason": reason,
            "flagged_at": time.time(),
            "original_key": cache_key,
        })
        # Le flag expire en même temps que le cache le plus long (24h)
        await self._client.setex(flag_key, settings.cache_ttl_historical, payload)
        await self._increment_stat("flags:created")
        logger.info("Flag deprecated posé : %s (raison: %s)", flag_key, reason)

    async def remove_flag(self, cache_key: str) -> bool:
        """
        Retire un flag deprecated (ex: après validation par un admin).

        Args:
            cache_key: La clé Redis COMPLÈTE dont on retire le flag.
        """
        flag_key = self.PREFIX_FLAG + cache_key
        removed = await self._client.delete(flag_key)
        if removed:
            logger.info("Flag deprecated retiré : %s", flag_key)
        return removed > 0

    async def _is_deprecated(self, cache_key: str) -> bool:
        """
        Vérifie si une entrée cache est flaggée deprecated.
        Méthode interne utilisée par tous les get_*_cache.

        Args:
            cache_key: La clé Redis COMPLÈTE à vérifier.
        """
        flag_key = self.PREFIX_FLAG + cache_key
        return await self._client.exists(flag_key) > 0

    async def get_flag_info(self, cache_key: str) -> dict | None:
        """
        Retourne les détails du flag (raison, date, etc.).
        Utile pour l'audit et le debugging.

        Args:
            cache_key: La clé Redis COMPLÈTE.
        """
        flag_key = self.PREFIX_FLAG + cache_key
        raw = await self._client.get(flag_key)
        if raw is None:
            return None
        return self._deserialize(raw)

    # ==========================================================
    # 5. SESSIONS CONVERSATIONNELLES
    # ==========================================================
    #
    # TODO Sprint 2 : Remplacer la lecture-modification-écriture
    # complète par une Redis List (RPUSH/LRANGE) pour éviter les
    # pertes de messages en cas d'accès concurrents. Pour le
    # Sprint 1, la solution actuelle est suffisante car chaque
    # session est liée à un seul utilisateur.
    #

    async def create_session(self, session_id: str, user_id: str) -> None:
        """
        Crée une nouvelle session conversationnelle.
        """
        key = self.PREFIX_SESSION + session_id
        session_data = self._serialize({
            "user_id": user_id,
            "created_at": time.time(),
            "history": [],
        })
        await self._client.setex(key, settings.cache_ttl_session, session_data)
        logger.debug("Session créée : %s pour user %s", session_id, user_id)

    async def add_to_session(
        self, session_id: str, role: str, content: str
    ) -> None:
        """
        Ajoute un message à l'historique de conversation.

        Args:
            session_id: Identifiant de la session.
            role: 'user' ou 'assistant'.
            content: Le contenu du message.
        """
        key = self.PREFIX_SESSION + session_id
        raw = await self._client.get(key)
        if raw is None:
            logger.warning("Session introuvable : %s", session_id)
            return

        session = self._deserialize(raw)
        session["history"].append({
            "role": role,
            "content": content,
            "timestamp": time.time(),
        })

        # Garder les 20 derniers messages pour limiter la taille
        session["history"] = session["history"][-20:]

        await self._client.setex(
            key, settings.cache_ttl_session, self._serialize(session)
        )

    async def get_session(self, session_id: str) -> dict | None:
        """
        Récupère une session complète avec son historique.
        Renouvelle le TTL à chaque accès (sliding expiration).
        """
        key = self.PREFIX_SESSION + session_id
        raw = await self._client.get(key)
        if raw is None:
            return None

        # Renouveler le TTL (sliding expiration)
        await self._client.expire(key, settings.cache_ttl_session)
        return self._deserialize(raw)

    async def get_session_history(self, session_id: str) -> list[dict]:
        """
        Retourne uniquement l'historique de conversation d'une session.
        """
        session = await self.get_session(session_id)
        if session is None:
            return []
        return session.get("history", [])

    async def delete_session(self, session_id: str) -> bool:
        """Supprime une session."""
        key = self.PREFIX_SESSION + session_id
        return await self._client.delete(key) > 0

    # ==========================================================
    # 6. RATE LIMITING
    # ==========================================================

    async def check_rate_limit(self, user_id: str) -> dict:
        """
        Vérifie et incrémente le compteur de requêtes d'un utilisateur.

        Retourne:
            {
                "allowed": True/False,
                "current": nombre actuel de requêtes,
                "limit": limite par minute,
                "remaining": requêtes restantes,
                "reset_in": secondes avant reset
            }
        """
        window = int(time.time() // 60)  # Fenêtre d'une minute
        key = f"{self.PREFIX_RATE}{user_id}:{window}"

        # Incrémenter le compteur
        current = await self._client.incr(key)

        # Expiration automatique après 60 secondes
        if current == 1:
            await self._client.expire(key, 60)

        ttl = await self._client.ttl(key)
        limit = settings.rate_limit_per_minute
        allowed = current <= limit

        if not allowed:
            await self._increment_stat("rate_limit:blocked")
            logger.warning(
                "Rate limit atteint pour user %s : %d/%d",
                user_id, current, limit,
            )

        return {
            "allowed": allowed,
            "current": current,
            "limit": limit,
            "remaining": max(0, limit - current),
            "reset_in": max(0, ttl),
        }

    # ==========================================================
    # 7. STATISTIQUES ET MÉTRIQUES
    # ==========================================================

    async def _increment_stat(self, stat_name: str, amount: int = 1) -> None:
        """Incrémente un compteur de statistique."""
        key = self.PREFIX_STATS + stat_name
        await self._client.incrby(key, amount)

    async def get_stats(self) -> dict:
        """
        Retourne toutes les statistiques du système.
        Utile pour le monitoring et le dashboard.
        """
        stats = {}
        keys = await self._scan_keys(self.PREFIX_STATS + "*")

        for key in keys:
            stat_name = key.replace(self.PREFIX_STATS, "")
            value = await self._client.get(key)
            stats[stat_name] = int(value) if value else 0

        # Calculer les taux de hit
        for prefix in ["cache:sql", "cache:plan", "cache:response"]:
            hits = stats.get(f"{prefix}:hits", 0)
            misses = stats.get(f"{prefix}:misses", 0)
            total = hits + misses
            stats[f"{prefix}:hit_rate"] = (
                round(hits / total * 100, 1) if total > 0 else 0.0
            )

        return stats

    async def reset_stats(self) -> int:
        """
        Remet toutes les statistiques à zéro.
        Retourne le nombre de compteurs supprimés.
        """
        keys = await self._scan_keys(self.PREFIX_STATS + "*")
        deleted = 0
        if keys:
            deleted = await self._client.delete(*keys)
        logger.info("Stats réinitialisées : %d compteurs supprimés.", deleted)
        return deleted

    # ==========================================================
    # 8. NAMESPACE FILE D'ATTENTE (PRÉPARATION CELERY)
    # ==========================================================

    async def enqueue_task(self, task_type: str, payload: dict) -> str:
        """
        Ajoute une tâche dans la file d'attente.
        Préparation pour Celery (Sprint 2/3).

        Retourne le task_id généré.
        """
        task_id = self._hash_key(f"{task_type}:{time.time()}")
        key = f"{self.PREFIX_QUEUE}{task_type}:{task_id}"
        task_data = self._serialize({
            "task_id": task_id,
            "type": task_type,
            "payload": payload,
            "status": "pending",
            "created_at": time.time(),
        })
        await self._client.setex(key, 3600, task_data)  # TTL 1h
        await self._increment_stat("queue:enqueued")
        logger.debug("Tâche enqueue : %s (%s)", task_id, task_type)
        return task_id

    async def get_task_status(self, task_type: str, task_id: str) -> dict | None:
        """Récupère le statut d'une tâche."""
        key = f"{self.PREFIX_QUEUE}{task_type}:{task_id}"
        raw = await self._client.get(key)
        if raw is None:
            return None
        return self._deserialize(raw)

    # ==========================================================
    # UTILITAIRES GÉNÉRAUX
    # ==========================================================

    async def flush_cache(self, namespace: str | None = None) -> int:
        """
        Vide le cache d'un namespace spécifique ou tout le cache.
        Usage réservé aux admins / debugging.

        Args:
            namespace: 'sql', 'plan', 'response', ou None pour tout.
        """
        prefix_map = {
            "sql": self.PREFIX_SQL,
            "plan": self.PREFIX_PLAN,
            "response": self.PREFIX_RESPONSE,
        }

        if namespace and namespace in prefix_map:
            pattern = prefix_map[namespace] + "*"
        elif namespace is None:
            pattern = "cache:*"
        else:
            raise ValueError(f"Namespace invalide : {namespace}")

        keys = await self._scan_keys(pattern)
        deleted = 0
        if keys:
            deleted = await self._client.delete(*keys)

        logger.info("Cache vidé (%s) : %d clés supprimées.", namespace or "all", deleted)
        return deleted

    async def get_cache_info(self) -> dict:
        """
        Retourne un résumé de l'état du cache :
        nombre de clés par namespace, mémoire utilisée.
        """
        info = await self._client.info("memory")
        namespaces = {
            "sql": self.PREFIX_SQL,
            "plan": self.PREFIX_PLAN,
            "response": self.PREFIX_RESPONSE,
            "flags": self.PREFIX_FLAG,
            "sessions": self.PREFIX_SESSION,
            "queue": self.PREFIX_QUEUE,
        }

        counts = {}
        for name, prefix in namespaces.items():
            keys = await self._scan_keys(prefix + "*")
            counts[name] = len(keys)

        return {
            "keys_by_namespace": counts,
            "total_keys": sum(counts.values()),
            "memory_used": info.get("used_memory_human", "N/A"),
            "memory_peak": info.get("used_memory_peak_human", "N/A"),
        }


redis_client = RedisClient()
