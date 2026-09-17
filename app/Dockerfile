# TradFilez — image minimale, une seule dépendance : Python.
FROM python:3.12-slim

# Les bibliothèques optionnelles débloquent Word, Excel, PDF et le HTML proprement.
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

WORKDIR /srv
COPY . /srv

# 8 Mo de corps de requête au maximum, cache dans un volume montable.
ENV PORT=8000 PYTHONUNBUFFERED=1 PASSERELLE_DATA=/srv/data
EXPOSE 8000

# Un process unique, sans shell intermédiaire : SIGTERM propre.
CMD ["python3", "server.py", "--host", "0.0.0.0"]
