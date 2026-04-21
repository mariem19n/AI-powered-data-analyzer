import psycopg2

print("Test connexion PostgreSQL...")

conn = psycopg2.connect(
    host="127.0.0.1",
    port=5432,
    dbname="analyzer_db",
    user="analyzer",
    password="analyzer_pg_123"
)

print("Connexion réussie !")

cur = conn.cursor()
cur.execute("SELECT current_database(), current_user;")
print(cur.fetchone())

cur.close()
conn.close()