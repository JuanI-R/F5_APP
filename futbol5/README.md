# ⚽ Fútbol 5 — Armador de Equipos

App para armar equipos equilibrados de fútbol 5, con historial de partidos, estadísticas y sistema de opiniones entre jugadores.

---

## 🗂 Estructura

```
futbol5/
├── backend/
│   ├── app.py           ← FastAPI backend
│   ├── requirements.txt
│   └── Procfile         ← Para Railway / Render
└── frontend/
    └── index.html       ← App completa en un solo archivo
```

---

## 🚀 Deploy gratuito

### Opción A — Todo junto (recomendado)

El backend puede servir el frontend directamente. Poné `index.html` en una carpeta `frontend/` al lado de `app.py` y Railway/Render lo va a servir en `/`.

### Backend → Railway (gratis)

1. Creá cuenta en [railway.app](https://railway.app)
2. "New Project" → "Deploy from GitHub" (subí la carpeta `backend/`)
3. Railway detecta el `Procfile` automáticamente
4. Variables de entorno (opcionales):
   - `DATABASE_URL` → si querés usar PostgreSQL en lugar de SQLite
5. Copiá la URL pública que te da Railway (ej: `https://futbol5.up.railway.app`)

### Frontend → Netlify (gratis)

1. Creá cuenta en [netlify.com](https://netlify.com)
2. Arrastrá la carpeta `frontend/` al dashboard de Netlify
3. Listo — te da una URL pública
4. En la app, entrá al panel Admin e ingresá la URL de Railway como "URL del backend"

### Opción B — Solo local

```bash
cd backend
pip install -r requirements.txt
uvicorn app:app --reload
# Abrir frontend/index.html en el browser
```

> En el panel Admin de la app podés configurar la URL del backend.

---

## 🔑 PIN de admin

Por defecto el PIN es `1234`. Podés cambiarlo desde el panel de configuración dentro de Admin.

El admin puede:
- Crear/editar/eliminar jugadores
- Agendar partidos
- Cargar resultados y rankings

---

## ⚙️ Funcionalidades

- **Jugadores** con 6 atributos (rango min/max): Disparo, Pase, Defensa, Visión, Físico, Velocidad
- **Arqueros** con ponderación especial
- **Autoevaluación** y **opiniones entre jugadores**
- **Sinergias**: cuánto le gusta a cada uno jugar con otro
- **Generador automático** de equipos equilibrados (10 jugadores → 2 equipos de 5)
- **Historial** de partidos con resultados y rankings
- **Tendencias** de rendimiento (▲ alza / ▼ baja / — parejo)
- **Estadísticas** completas por jugador con % de victorias
