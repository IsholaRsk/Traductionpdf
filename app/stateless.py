"""TradFilez — API sans état, pour les hébergeurs « serverless ».

Sur un hébergeur sans état (Vercel, AWS Lambda), chaque requête peut tomber
    sur une instance différente
différente : la file de tâches en mémoire du serveur local n'a donc aucun sens.
Ce module propose le même travail en trois appels indépendants, le navigateur
gardant tout entre les deux :

    1. POST /api/open     fichier + meta  →  unités à traduire, celles déjà en cache
    2. POST /api/translate  lots de texte  →  traductions (le serveur ne retient rien)
    3. POST /api/build      fichier + traductions  →  fichier reconstruit

Le cache de paires reste utilisé, mais seulement comme optimisation : sa perte
ne casse rien. Les limites par appel (`TRADFILEZ_WINDOW`, `TRADFILEZ_WINDOW_CHARS`)
existent pour finir une requête avant la mort du « runtime » de l'hébergeur.
"""

from __future__ import annotations

import base64
import os

import engines as eng
import formats as fmt


def _num(name, default):
    try:
        return max(1, int(eng.env(name, default)))
    except ValueError:
        return default


MAX_ITEMS = _num("window", 60)
MAX_CHARS = _num("window_chars", 12000)
PREVIEW_LIMIT = 24000


class StatelessError(Exception):
    """Erreur renvoyée telle quelle au navigateur (message lisible)."""


# --------------------------------------------------------------------------- #
# découpage
# --------------------------------------------------------------------------- #

def chunks(units, todo, limit_items=MAX_ITEMS, limit_chars=MAX_CHARS):
    """Regroupe les indices à traduire en lots bornés en nombre *et* en caractères.

    Un lot trop lourd ferait exploser le délai de l'appel : on coupe donc aussi
    selon le nombre de caractères, ce qui garde le pire cas sous la seconde
    dizaines de secondes.
    """
    out, cur, chars = [], [], 0
    for i in todo:
        size = len(units[i] or "")
        if cur and (len(cur) >= limit_items or chars + size > limit_chars):
            out.append(cur)
            cur, chars = [], 0
        cur.append(i)
        chars += size
    if cur:
        out.append(cur)
    return out


def _doc(meta, blob):
    name = (meta.get("filename") or "document.txt").strip() or "document.txt"
    try:
        return fmt.load(name, blob, meta.get("options") or {})
    except fmt.Unsupported as exc:
        raise StatelessError(str(exc)) from exc


def _chain(meta):
    chain = [e for e in eng.build_engines(meta.get("config") or {}) if e is not None]
    if not chain:
        raise StatelessError("Aucun moteur disponible : vérifiez les réglages.")
    return chain


# --------------------------------------------------------------------------- #
# les trois appels
# --------------------------------------------------------------------------- #

def open_file(meta, blob):
    """Analyse le fichier et renvoie ce qui reste à traduire."""
    if not (meta.get("tgt") or "").strip():
        raise StatelessError("Langue cible manquante.")
    doc = _doc(meta, blob)
    src = (meta.get("src") or "auto").strip() or "auto"
    if src == "auto":
        probe = " ".join(doc.units[i] for i in doc.todo[:80])[:3000]
        src = eng.detect_language(probe or doc.preview_in()[:3000])
    if not doc.todo:
        raise StatelessError("Aucun texte à traduire dans ce fichier.")

    cache = eng.CACHE
    cached, pending = {}, []
    for i in doc.todo:
        hit = cache.get("shared", src, meta["tgt"], doc.units[i])
        if hit is None:
            pending.append(i)
        else:
            cached[str(i)] = hit

    return {
        "name": doc.name,
        "kind": doc.kind,
        "kindLabel": doc.label,
        "note": doc.note or "",
        "src": src,
        "count": len(doc.todo),
        "units": doc.unit_count,
        "pending": [{"i": i, "t": doc.units[i]} for i in pending],
        "cached": cached,
        "batches": len(chunks(doc.units, pending)),
        "window": {"items": MAX_ITEMS, "chars": MAX_CHARS},
        "truncated": bool(getattr(doc, "truncated", False)),
    }


def translate(meta, items, started=None):
    """Traduit une liste de segments, sans rien garder ailleurs que le cache."""
    if not items:
        return {"translations": [], "translated": 0}
    if len(items) > MAX_ITEMS:
        raise StatelessError(
            f"Trop de segments d'un coup ({len(items)} > {MAX_ITEMS})."
        )
    if sum(len(t or "") for t in items) > MAX_CHARS:
        raise StatelessError("Lot trop long : réduisez le nombre de caractères envoyés.")

    tgt = (meta.get("tgt") or "").strip()
    if not tgt:
        raise StatelessError("Langue cible manquante.")
    src = (meta.get("src") or "auto").strip() or "auto"
    if src == "auto":
        src = eng.detect_language(" ".join(items[:80])[:3000])
    chain = _chain(meta)

    # chaque segment peut dépasser la limite du moteur : on le coupe puis on recolle
    slots, flat = {}, []
    for n, text in enumerate(items):
        pieces = eng.split_long(text or "", chain[0].max_chars) or [""]
        slots[n] = [len(flat) + k for k in range(len(pieces))]
        flat.extend(pieces)

    got = [None] * len(flat)
    errors, engine_used = [], []
    for engine in chain:
        todo = [i for i, v in enumerate(got) if v is None]
        if not todo:
            break
        try:
            out = engine.translate_lines([flat[i] for i in todo], src, tgt)
        except eng.EngineError as exc:
            errors.append(f"{engine.label} : {exc}")
            continue
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{engine.label} : {type(exc).__name__} {exc}")
            continue
        if len(out) != len(todo):
            errors.append(f"{engine.label} : désaccord d'alignement, moteur écarté")
            continue
        if engine.id not in engine_used:
            engine_used.append(engine.id)
        for i, src_text, value in zip(todo, [flat[i] for i in todo], out):
            value = (value or "").strip() or src_text
            got[i] = value
            eng.CACHE.put("shared", src, tgt, src_text, value)

    translations = []
    for n, text in enumerate(items):
        joined = " ".join(got[i] or text for i in slots[n]).strip()
        translations.append(joined or text or "")
    done = sum(1 for n, t in enumerate(translations) if (t or "") != (items[n] or ""))
    if not done and errors:
        raise StatelessError("Aucun moteur n'a répondu — " + " · ".join(errors[:2]))
    return {
        "translations": translations,
        "translated": done,
        "engine": engine_used[0] if engine_used else "",
        "engines": engine_used,
        "errors": errors,
        "src": src,
    }


def build(meta, blob, translations):
    """Recolle les traductions et renvoie le fichier reconstruit."""
    doc = _doc(meta, blob)
    by_index = {int(k): v for k, v in (translations or {}).items()}
    recus = [i for i in doc.todo if (by_index.get(i) or "").strip()]
    if doc.todo and not recus:
        # sans rien à reposer, on rendrait l'original en le croyant traduit : on refuse
        raise StatelessError(
            "Aucune traduction reçue pour ce fichier : relancez la traduction des segments."
        )
    manquants = len(doc.todo) - len(recus)
    full = []
    for i in range(doc.unit_count):
        if doc.units[i] is None:
            full.append("")
        else:
            full.append((by_index.get(i) or "").strip() or doc.units[i])
    try:
        data, name, preview_in, preview_out = doc.rebuild(full)
    except Exception as exc:  # noqa: BLE001
        raise StatelessError(f"Reconstruction impossible : {type(exc).__name__} {exc}") from exc
    return {
        "name": name,
        "size": len(data),
        "b64": base64.b64encode(data).decode("ascii"),
        "in": preview_in[:PREVIEW_LIMIT],
        "out": preview_out[:PREVIEW_LIMIT],
        "kind": doc.kind,
        "kindLabel": doc.label,
        "note": doc.note or "",
        "count": len(doc.todo),
        "total": len(doc.todo),
        "missing": manquants,
    }
