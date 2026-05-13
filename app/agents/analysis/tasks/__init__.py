"""
app/agents/analysis/tasks
Tasks de l'Analysis Agent.

L'import des modules concrets ci-dessous déclenche leur enregistrement
auprès du registry via @register_task. C'est l'unique mécanisme par lequel
les tasks deviennent disponibles à get_task() — il n'y a pas de liste
hardcodée nulle part.

Pour ajouter une nouvelle task :
  1. créer un fichier dans ce dossier (ex: anomaly.py)
  2. décorer la classe avec @register_task
  3. l'importer ici pour déclencher l'enregistrement
"""

# Imports de side-effect : chacun enregistre sa task via @register_task.
# noqa: F401 — imports volontairement non-utilisés directement.
from app.agents.analysis.tasks import descriptive  # noqa: F401
from app.agents.analysis.tasks import external_summary  # noqa: F401
from app.agents.analysis.tasks import hybrid_summary  # noqa: F401
from app.agents.analysis.tasks import anomaly_detection  # noqa: F401  

# API publique du sous-package.
from app.agents.analysis.tasks.base import (
    AnalysisTask,
    TaskResult,
    get_task,
    list_registered_tasks,
    register_task,
)

__all__ = [
    "AnalysisTask",
    "TaskResult",
    "get_task",
    "list_registered_tasks",
    "register_task",
]
