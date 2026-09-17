"""Passerelle — moteurs de traduction et cache.

Uniquement la bibliothèque standard (urllib, sqlite3) : l'application tourne
sans dépendance installée. Les moteurs exposent tous la même méthode
`translate_lines(lignes, src, tgt) -> list[str]`, alignée ligne à ligne, ce
qui garantit la reconstruction fidèle des fichiers.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = "Passerelle/1.0 (+traduction de fichiers)"
SENTENCE_SPLIT = re.compile(r"(?<=[.!?。．！？؟])\s+")

# --------------------------------------------------------------------------- #
# erreurs & HTTP
# --------------------------------------------------------------------------- #


class EngineError(Exception):
    """`retryable` : on peut retenter ; `fatal` : on passe au moteur suivant."""

    def __init__(self, message, *, retryable=True, fatal=False, quota=False):
        super().__init__(message)
        self.retryable = retryable
        self.fatal = fatal
        self.quota = quota


def http(url, *, method="POST", headers=None, data=None, timeout=45):
    """Appel HTTP minimaliste -> (statut, texte brut)."""
    req = urllib.request.Request(url, method=method)
    req.add_header("User-Agent", USER_AGENT)
    req.add_header("Accept", "application/json, text/plain, */*")
    for key, value in (headers or {}).items():
        if value is not None:
            req.add_header(key, value)
    if isinstance(data, dict):
        body = json.dumps(data).encode("utf-8")
        req.add_header("Content-Type", "application/json; charset=utf-8")
    elif isinstance(data, (bytes, bytearray)):
        body = bytes(data)
    elif isinstance(data, str):
        body = data.encode("utf-8")
    else:
        body = None
    try:
        with urllib.request.urlopen(req, data=body, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        try:
            raw = exc.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            raw = ""
        return exc.code, raw
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise EngineError(f"réseau injoignable : {getattr(exc, 'reason', exc)}", retryable=True) from exc


def _loads(raw):
    try:
        return json.loads(raw)
    except Exception:  # noqa: BLE001
        return None


# --------------------------------------------------------------------------- #
# cache des paires traduites (réexécutions, fichiers avec répétitions)
# --------------------------------------------------------------------------- #


class PairCache:
    def __init__(self, path):
        self.path = path
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS pairs ("
            "k TEXT PRIMARY KEY, src TEXT, tgt TEXT, engine TEXT, "
            "inp TEXT, outp TEXT, ts REAL)"
        )
        self.hits = 0
        self.misses = 0

    @staticmethod
    def _key(engine, src, tgt, text):
        return f"{engine}|{src}|{tgt}|" + re.sub(r"\s+", " ", text).strip()

    def get(self, engine, src, tgt, text):
        key = self._key(engine, src, tgt, text)
        with self._lock:
            row = self._conn.execute("SELECT outp FROM pairs WHERE k=?", (key,)).fetchone()
        if row:
            self.hits += 1
            return row[0]
        self.misses += 1
        return None

    def put(self, engine, src, tgt, text, translation):
        if not translation or len(translation) > 100000:
            return
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO pairs (k, src, tgt, engine, inp, outp, ts) VALUES (?,?,?,?,?,?,?)",
                (self._key(engine, src, tgt, text), src, tgt, engine, text, translation, time.time()),
            )

    def stats(self):
        try:
            with self._lock:
                n = self._conn.execute("SELECT COUNT(*) FROM pairs").fetchone()[0]
            return {"pairs": n, "bytes": os.path.getsize(self.path) if os.path.exists(self.path) else 0}
        except Exception:  # noqa: BLE001
            return {"pairs": 0, "bytes": 0}

    def clear(self):
        with self._lock:
            self._conn.execute("DELETE FROM pairs")
            self._conn.execute("VACUUM")

    def recent(self, limit=8):
        with self._lock:
            rows = self._conn.execute(
                "SELECT src, tgt, engine, inp, outp, ts FROM pairs ORDER BY ts DESC LIMIT ?", (limit,)
            ).fetchall()
        return [
            {"src": r[0], "tgt": r[1], "engine": r[2], "input": r[3][:150], "output": r[4][:150], "ts": r[5]}
            for r in rows
        ]


def _cache_path():
    """Répertoire du cache : `$PASSERELLE_DIR`, sinon `app/data` à côté du code."""
    folder = (os.environ.get("PASSERELLE_DIR") or "").strip()
    if not folder:
        folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    return os.path.join(folder, "pairs.sqlite3")


CACHE = PairCache(_cache_path())


# --------------------------------------------------------------------------- #
# découpage
# --------------------------------------------------------------------------- #


def split_long(text, limit):
    """Découpe un long paragraphe en morceaux de `limit` caractères, aux phrases."""
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= limit:
        return [text]
    chunks, cur = [], ""
    for sentence in SENTENCE_SPLIT.split(text):
        if not sentence or not sentence.strip():
            continue
        if cur and len(cur) + 1 + len(sentence) > limit:
            chunks.append(cur.strip())
            cur = sentence
        else:
            cur = f"{cur} {sentence}".strip()
    if cur.strip():
        chunks.append(cur.strip())
    final = []
    for piece in chunks:
        while len(piece) > limit:
            cut = piece.rfind(" ", 0, limit)
            if cut < int(limit * 0.5):
                cut = limit
            final.append(piece[:cut].strip())
            piece = piece[cut:].strip()
        if piece:
            final.append(piece)
    return final or [text[:limit]]


def pack_pairs(pairs, max_chars, max_lines):
    """Groupe des (indice, texte) en lots respectant les limites du moteur."""
    groups, cur, size = [], [], 0
    for item in pairs:
        need = len(item[1]) + 1
        if cur and (size + need > max_chars or len(cur) >= max_lines):
            groups.append(cur)
            cur, size = [], 0
        cur.append(item)
        size += need
    if cur:
        groups.append(cur)
    return groups


def realign(raw, expected):
    """Retrouve `expected` lignes dans la sortie d'un moteur (None si peu fiable)."""
    if raw is None or expected <= 0:
        return None
    lines = [ln.strip() for ln in raw.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    if len(lines) == expected:
        return lines
    if expected < len(lines) <= expected * 1.6 + 2:
        merged = lines[: expected - 1]
        merged.append(" ".join(x for x in lines[expected - 1:] if x).strip())
        return merged
    return None


# --------------------------------------------------------------------------- #
# détection de langue (repli hors-ligne)
# --------------------------------------------------------------------------- #

STOPWORDS = {
    "fr": ["le", "la", "les", "des", "une", "est", "dans", "qui", "pour", "avec", "sur", "pas", "que", "vous", "nous", "cette", "être"],
    "en": ["the", "and", "of", "to", "is", "in", "that", "for", "you", "with", "have", "this", "not", "are", "from", "was"],
    "es": ["el", "la", "los", "una", "que", "de", "para", "con", "está", "nosotros", "pero", "como", "más", "este", "por"],
    "pt": ["o", "a", "os", "uma", "que", "de", "para", "com", "está", "nós", "mas", "como", "mais", "este", "você", "não"],
    "it": ["il", "la", "che", "di", "per", "con", "questo", "sono", "come", "anche", "molto", "nella", "della", "non"],
    "de": ["der", "die", "und", "das", "ist", "nicht", "mit", "für", "auf", "den", "ein", "eine", "auch", "werden", "sie"],
    "nl": ["de", "het", "een", "van", "en", "is", "niet", "met", "voor", "op", "dit", "dat", "aan", "je", "er"],
    "ar": ["ال", "في", "من", "على", "هذا", "هذه", "إلى", "عن", "كل", "بين", "كان", "التي"],
    "zh": ["的", "是", "了", "在", "和", "我", "有", "不", "这", "们", "中", "为"],
    "ja": ["の", "は", "に", "で", "を", "が", "た", "ます", "した", "ない", "これ", "ある"],
    "ko": ["의", "는", "에", "로", "을", "를", "이", "그", "있다", "하지", "것", "하고"],
    "ru": ["и", "в", "не", "что", "на", "я", "с", "как", "это", "для", "но", "он"],
    "uk": ["і", "в", "не", "що", "на", "як", "це", "для", "та", "він", "ми", "їх"],
    "tr": ["bir", "bu", "için", "ile", "değil", "çok", "gibi", "daha", "var", "olan", "ve"],
    "pl": ["nie", "jest", "się", "jak", "dla", "tego", "oraz", "aby", "czy", "są", "ale"],
    "ro": ["și", "de", "care", "pentru", "este", "cu", "la", "un", "o", "mai", "acest", "din"],
    "el": ["το", "και", "για", "με", "από", "είναι", "στην", "που", "ότι", "του", "een"],
    "he": ["של", "על", "את", "אני", "לא", "זה", "עם", "כל", "מה", "יש", "הזה"],
    "hi": ["और", "है", "के", "में", "से", "को", "का", "की", "हैं", "यह", "पर"],
    "id": ["yang", "dan", "di", "ini", "untuk", "dengan", "tidak", "pada", "adalah", "itu", "dari"],
    "vi": ["và", "của", "cho", "không", "là", "được", "trong", "này", "với", "có", "để"],
    "sv": ["och", "att", "det", "som", "för", "med", "inte", "på", "den", "har", "de"],
    "da": ["og", "at", "det", "som", "for", "med", "ikke", "den", "har", "de", "er"],
    "fi": ["ja", "että", "se", "ei", "on", "kun", "niin", "ovat", "vain", "kanssa", "tai"],
    "cs": ["a", "je", "pro", "nebo", "že", "jak", "to", "s", "na", "jsou", "ale"],
    "hu": ["és", "hogy", "nem", "az", "egy", "van", "még", "mint", "ez", "csak", "volt"],
    "sw": ["na", "ya", "katika", "kwa", "hii", "si", "ni", "ana", "watu", "kutoka", "ya"],
    "no": ["og", "i", "jeg", "det", "at", "en", "for", "med", "å", "på", "er"],
}


def detect_language(text):
    """Détection légère par profils de mots outils -> code ISO-639-1."""
    sample = (text or "")[:4000].lower()
    if not sample.strip():
        return "en"
    counts = {}
    for word in re.findall(r"[\w'’À-ÿ\u0400-\u04FF\u0590-\u05FF\u0600-\u06FF\u3040-\u30FF\u4E00-\u9FFF]+", sample):
        counts[word] = counts.get(word, 0) + 1
    total = max(sum(counts.values()), 1)
    cjk = sum(1 for ch in sample if "\u4e00" <= ch <= "\u9fff")
    kana = sum(1 for ch in sample if "\u3040" <= ch <= "\u30ff")
    hangul = sum(1 for ch in sample if "\uac00" <= ch <= "\ud7af")
    scores = {}
    for lang, stops in STOPWORDS.items():
        hits = sum(counts.get(s, 0) for s in stops)
        if lang == "zh" and cjk / total > 0.05:
            hits += int(cjk * 1.5)
        if lang == "ja" and kana / total > 0.02:
            hits += int(kana * 2)
        if lang == "ko" and hangul / total > 0.02:
            hits += int(hangul * 2)
        if lang == "ar" and sum(1 for ch in sample if "\u0600" <= ch <= "\u06ff") / total > 0.1:
            hits += 8
        if lang == "he" and sum(1 for ch in sample if "\u0590" <= ch <= "\u05ff") / total > 0.1:
            hits += 8
        scores[lang] = hits
    best = max(scores.items(), key=lambda kv: kv[1])
    return best[0] if best[1] > 0 else "en"


LANG_NAMES = {
    "fr": "français", "en": "anglais", "es": "espagnol", "pt": "portugais", "it": "italien",
    "de": "allemand", "nl": "néerlandais", "ar": "arabe", "zh": "chinois", "ja": "japonais",
    "ko": "coréen", "ru": "russe", "uk": "ukrainien", "tr": "turc", "pl": "polonais",
    "ro": "roumain", "el": "grec", "he": "hébreu", "hi": "hindi", "id": "indonésien",
    "vi": "vietnamien", "sv": "suédois", "da": "danois", "no": "norvégien", "fi": "finnois",
    "cs": "tchèque", "hu": "hongrois", "sw": "swahili", "ca": "catalan", "bg": "bulgare",
    "auto": "détection automatique",
}


def lang_label(code):
    return LANG_NAMES.get(code, code or "?")


# --------------------------------------------------------------------------- #
# moteurs
# --------------------------------------------------------------------------- #


class BaseEngine:
    id = "base"
    label = "Base"
    hint = ""
    needs_key = False
    max_chars = 480          # charge maximale d'une requête
    max_lines = 40           # nombre maximal de lignes par requête
    concurrency = 2
    min_interval = 0.12      # seconde minimale entre deux requêtes
    retries = 3

    def __init__(self, config=None):
        self.config = config or {}
        self._gate = threading.Semaphore(self.concurrency)
        self._rate = threading.Lock()
        self._next_slot = 0.0

    # -- réglages ----------------------------------------------------------- #
    def ready(self):
        if not self.needs_key:
            return True
        return bool((self.config.get("api_key") or "").strip() or (self.config.get("base_url") or "").strip())

    def _throttle(self):
        if self.min_interval <= 0:
            return
        with self._rate:
            now = time.time()
            wait = self._next_slot - now
            self._next_slot = max(now, self._next_slot) + self.min_interval
        if wait > 0:
            time.sleep(wait)

    def call(self, fn, *args, **kwargs):
        with self._gate:
            self._throttle()
            return fn(*args, **kwargs)

    @staticmethod
    def clean(text):
        # un retour à la ligne venu du moteur ne doit pas casser l'alignement :
        # une unité = une ligne, comme à l'entrée (formats.flat).
        return re.sub(r"\s*\n\s*", " ", re.sub(r"[ \t]{2,}", " ", (text or "").replace("\r", "").strip()))

    # -- API publique ------------------------------------------------------- #
    def translate_lines(self, lines, src, tgt):
        """Traduit ligne à ligne, en ré-éclaircissant les lignes trop longues."""
        pairs = []
        for i, line in enumerate(lines):
            text = line if line.strip() else " "
            if len(text) > self.max_chars:
                for piece in split_long(text, max(120, self.max_chars - 20)):
                    pairs.append((i, piece))
            else:
                pairs.append((i, text))
        results = {}
        for group in pack_pairs(pairs, self.max_chars, self.max_lines):
            out = self._api_lines([t for _, t in group], src, tgt)
            for (i, _), value in zip(group, out):
                results.setdefault(i, []).append(value)
        return [" ".join(results.get(i, [])).strip() for i in range(len(lines))]

    def _api_lines(self, texts, src, tgt):
        """Une ligne par unité : passage par `_api`, réelignement, repli unité par unité."""
        payload = "\n".join(texts)
        raw = None
        last_error = None
        for attempt in range(self.retries):
            try:
                raw = self.call(self._api, payload, src, tgt)
                last_error = None
                break
            except EngineError as exc:
                last_error = exc
                if exc.fatal or not exc.retryable or attempt == self.retries - 1:
                    raise
                time.sleep(0.8 * (attempt + 1) + 0.4 * attempt)
        aligned = realign(raw, len(texts))
        if aligned is not None:
            return [self.clean(a) for a in aligned]
        if last_error is not None:
            raise last_error
        out = []
        for text in texts:
            out.append(self.clean(self.call(self._api, text, src, tgt)).replace("\n", " "))
        return out

    def _api(self, text, src, tgt):  # pragma: no cover - interface
        raise NotImplementedError

    def probe(self):
        try:
            sample = "The report outlines a soft, sober and minimal design for the site."
            lines = self._api_lines([sample], "en", "fr")
            first = lines[0] if isinstance(lines, (list, tuple)) and lines else lines
            return {"ok": True, "sample": self.clean(first)[:200]}
        except EngineError as exc:
            return {"ok": False, "error": str(exc)}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"{type(exc).__name__} : {exc}"}


class MyMemoryEngine(BaseEngine):
    id = "mymemory"
    label = "MyMemory"
    hint = "Gratuit, sans clé. Idéal pour démarrer ; quota quotidien limité (élargi avec un e-mail)."
    max_chars = 460
    max_lines = 22
    concurrency = 3
    min_interval = 0.15

    def _api(self, text, src, tgt):
        params = {"q": text, "langpair": f"{src}|{tgt}", "raw": 3}
        email = (self.config.get("email") or "").strip()
        if email and "@" in email:
            params["de"] = email
        status, raw = http(
            "https://api.mymemory.translated.net/get?" + urllib.parse.urlencode(params),
            method="GET", timeout=45,
        )
        data = _loads(raw)
        body = ""
        if isinstance(_loads(raw), dict):
            body = ((data.get("responseData") or {}).get("translatedText") or "").strip()
        upper = body.upper()
        if data.get("quotaFinished") is True or "FREE TRANSLATIONS FOR TODAY" in upper or "QUOTA" in upper:
            delay = re.search(r"NEXT AVAILABLE IN\s+(.+?)\s+VISIT", body, re.I)
            hint = ""
            if delay:
                raw_delay = delay.group(1).upper()
                hours = re.search(r"(\d+)\s*HOURS?", raw_delay)
                minutes = re.search(r"(\d+)\s*MINUTES?", raw_delay)
                if hours or minutes:
                    hint = " (nouveau quota dans " + " ".join(
                        filter(None, [f"{hours.group(1)} h" if hours else "",
                                      f"{minutes.group(1)} min" if minutes else ""])
                    ) + ")"
                else:
                    hint = f" (nouveau quota dans {delay.group(1).strip()})"
            raise EngineError(
                "quota gratuit du jour atteint" + hint + " — indiquez un e-mail dans les réglages, ou choisissez DeepL / IA.",
                fatal=True, quota=True,
            )
        if status == 429:
            raise EngineError("limite de débit atteinte, nouvelle tentative…")
        if not isinstance(data, dict):
            raise EngineError(f"réponse inattendue (HTTP {status})")
        if "LIMIT EXCEEDED" in upper:
            raise EngineError("lot trop long pour ce moteur", fatal=True, retryable=False)
        if data.get("responseStatus") != 200 and not body:
            raise EngineError(f"erreur {data.get('responseStatus')} {data.get('responseDetails') or ''}".strip())
        return body


class LibreTranslateEngine(BaseEngine):
    id = "libretranslate"
    label = "LibreTranslate"
    hint = "Traduction open source. Le démon public est lent (1 000 car., 3 req./min) : réservez-le aux petits fichiers, ou pointez votre propre instance."
    max_chars = 900
    max_lines = 25
    concurrency = 2
    min_interval = 1.0

    def __init__(self, config=None):
        super().__init__(config)
        self.base = ((self.config.get("base_url") or "https://translate.disroot.org").strip().rstrip("/"))
        if self.config.get("min_interval"):
            try:
                self.min_interval = float(self.config["min_interval"])
            except (TypeError, ValueError):
                pass

    def ready(self):
        return True

    def _api(self, text, src, tgt):
        payload = {"q": text, "source": src, "target": tgt, "format": "text"}
        key = (self.config.get("api_key") or "").strip()
        if key:
            payload["api_key"] = key
        status, raw = http(self.base + "/translate", data=payload, timeout=90)
        data = _loads(raw)
        if status == 429:
            raise EngineError(f"limite de débit de {self.base} — augmentez le délai ou auto-hébergez")
        if not isinstance(data, dict) or "translatedText" not in data:
            detail = str(data)[:180] if data else f"HTTP {status}"
            raise EngineError(detail, fatal=status in (401, 403, 404))
        return data["translatedText"]

    def detect(self, text):
        try:
            status, raw = http(self.base + "/detect", data={"q": text[:1200]}, timeout=25)
            data = _loads(raw)
            if status == 200 and isinstance(data, dict) and data.get("language"):
                return str(data["language"])[:8]
        except Exception:  # noqa: BLE001
            pass
        return None


class DeepLEngine(BaseEngine):
    id = "deepl"
    label = "DeepL"
    hint = "Votre clé. Qualité maximale, lots de 50 lignes, jusqu'à 500 000 caractères/mois en gratuit."
    max_chars = 4500
    max_lines = 50
    concurrency = 3
    min_interval = 0.06
    needs_key = True
    _map = {"en": "EN-US", "pt": "PT-BR", "zh": "ZH", "gr": "EL"}

    def __init__(self, config=None):
        super().__init__(config)
        self.api_key = (self.config.get("api_key") or "").strip()
        base = (self.config.get("base_url") or "").strip().rstrip("/")
        self.base = base or ("https://api-free.deepl.com" if self.api_key.endswith(":fx") else "https://api.deepl.com")

    def code(self, lang):
        return self._map.get(lang, (lang or "EN").upper())

    def _api_lines(self, texts, src, tgt):
        fields = {"auth_key": self.api_key, "target_lang": self.code(tgt)}
        if src and src != "auto":
            fields["source_lang"] = self.code(src)
        tone = (self.config.get("tone") or "").lower()
        if tone in ("soutenu", "formel", "literary") and self.code(tgt) in {"FR", "DE", "ES", "IT", "NL", "PL", "RU", "PT-BR"}:
            fields["formality"] = "more"
        elif tone in ("courant", "simple", "informel"):
            fields["formality"] = "less"
        body = urllib.parse.urlencode(fields, quote_via=urllib.parse.quote)
        for text in texts:
            body += "&text=" + urllib.parse.quote(text, safe="")
        status, raw = http(
            self.base + "/v2/translate", data=body.encode(),
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Authorization": "DeepL-Auth-Key " + self.api_key,
            }, timeout=120,
        )
        data = _loads(raw)
        if status != 200:
            msg = (data or {}).get("message", f"HTTP {status}") if isinstance(data, dict) else f"HTTP {status}"
            fatal = status in (401, 403) or "quota" in str(msg).lower() or "free" in str(msg).lower()
            raise EngineError(str(msg), retryable=not fatal, fatal=fatal, quota="quota" in str(msg).lower())
        trs = (data or {}).get("translations") or []
        if len(trs) != len(texts):
            raise EngineError("réponse partielle", fatal=True, retryable=False)
        return [self.clean(t.get("text")) for t in trs]

    def _api(self, text, src, tgt):  # pragma: no cover
        raise EngineError("DeepL traduit par lots de lignes", fatal=True, retryable=False)


class OpenAICompatEngine(BaseEngine):
    id = "llm"
    label = "IA (compatible OpenAI)"
    hint = "Votre clé : OpenAI, Mistral, Groq, OpenRouter, Ollama… Respecte listes, code et consignes de style."
    max_chars = 4200
    max_lines = 20
    concurrency = 2
    min_interval = 0.05
    needs_key = True
    retries = 2

    SYSTEM = (
        "Tu es un traducteur professionnel, natif de la langue cible. On te soumet des lignes extraites "
        "d'un fichier, chacune précédée d'un numéro et d'une tabulation. Renvoie exactement une ligne par "
        "ligne reçue, dans le même ordre, avec le même numéro suivi d'une tabulation. N'ajoute aucun "
        "commentaire, aucune ligne de regroupement, aucun guillemet superflu. Conserve à l'identique : "
        "balises, ponctuation technique, URL, variables, identifiants, chiffres, code, tabulations. "
        "Garde le sens, le ton et le registre de l'original, et traduis les titres, légendes et libellés "
        "comme un rédacteur natif le ferait."
    )

    def __init__(self, config=None):
        super().__init__(config)
        self.base = (self.config.get("base_url") or "https://api.openai.com/v1").strip().rstrip("/")
        self.api_key = (self.config.get("api_key") or "").strip()
        self.model = (self.config.get("model") or "gpt-4o-mini").strip()
        try:
            self.max_tokens = int(self.config.get("max_tokens") or 4096)
        except (TypeError, ValueError):
            self.max_tokens = 4096
        try:
            self.temperature = float(self.config.get("temperature") or 0.2)
        except (TypeError, ValueError):
            self.temperature = 0.2

    def system_prompt(self):
        tone = {
            "fluide": "Privilégie une langue naturelle et idiomatique plutôt qu'un mot à mot.",
            "fidele": "Reste très proche de la structure de l'original, sans embellir.",
            "soutenu": "Registre soutenu, phrases soignées.",
            "simple": "Registre courant, phrases courtes et claires.",
            "litteraire": "Rythme et vocabulaire soignés, comme une publication éditoriale.",
        }.get((self.config.get("tone") or "").lower(), "")
        extra = (self.config.get("glossary") or "").strip()
        prompt = self.SYSTEM
        if tone:
            prompt += " " + tone
        if extra:
            prompt += " Consignes du client à respecter absolument : " + extra[:1200]
        return prompt

    def parse(self, text, count):
        out, idx, buf = {}, None, []
        for line in (text or "").replace("\r", "").split("\n"):
            m = re.match(r"^\s*(\d{1,4})[\t.)\]|:-]?\s?(.*)$", line)
            if m and 0 <= int(m.group(1)) < count:
                if idx is not None and idx not in out:
                    out[idx] = "\n".join(buf).strip()
                idx, buf = int(m.group(1)), [m.group(2)]
            elif idx is not None:
                buf.append(line)
            elif line.strip():
                buf.append(line)
        if idx is not None and idx not in out:
            out[idx] = "\n".join(buf).strip()
        if count == 1:
            joined = (text or "").strip()
            joined = re.sub(r"^0[\t.)\|:-]?\s*", "", joined)
            return [self.clean(joined)]
        if len(out) == count and all(i in out for i in range(count)):
            return [self.clean(out[i]) for i in range(count)]
        return None

    def _api_lines(self, texts, src, tgt):
        header = f"Langue cible : {lang_label(tgt)}."
        if src and src != "auto":
            header = f"Langue source : {lang_label(src)}. {header}"
        body = header + "\n\n" + "\n".join(f"{i}\t{t}" for i, t in enumerate(texts))
        payload = {
            "model": self.model,
            "temperature": self.temperature,
            "max_tokens": min(self.max_tokens, max(400, len(body) * 3)),
            "messages": [{"role": "system", "content": self.system_prompt()}, {"role": "user", "content": body}],
        }
        status, raw = http(
            self.base + "/chat/completions", data=payload,
            headers={"Authorization": f"Bearer {self.api_key}"}, timeout=240,
        )
        data = _loads(raw)
        if status != 200:
            msg = ((data or {}).get("error") or {}).get("message") if isinstance(data, dict) else None
            msg = msg or (str(data)[:200] if data else f"HTTP {status}")
            fatal = status in (401, 403, 404)
            raise EngineError(str(msg)[:300], retryable=not fatal, fatal=fatal)
        try:
            content = data["choices"][0]["message"]["content"]
        except Exception as exc:  # noqa: BLE001
            raise EngineError("réponse de modèle inexploitable", fatal=True, retryable=False) from exc
        parsed = self.parse(content, len(texts))
        if parsed is None:
            raise EngineError("numérotation des lignes perdue", retryable=True)
        return parsed


ENGINES = {
    cls.id: cls for cls in (MyMemoryEngine, LibreTranslateEngine, DeepLEngine, OpenAICompatEngine)
}
# Chaîne automatique : on n'y met pas le démon public LibreTranslate (3 requêtes/minute),
# au risque de faire ramper un gros fichier ; on le sélectionne à la main.
FREE_ORDER = [DeepLEngine.id, OpenAICompatEngine.id, MyMemoryEngine.id]


def build_engines(config):
    """Chaîne de moteurs à essayer, du préféré aux replis."""
    config = dict(config or {})
    shared = {k: config.get(k) for k in ("tone", "glossary") if config.get(k)}
    made = {}
    for eid, cls in ENGINES.items():
        merged = dict(shared)
        merged.update(config.get(eid) or {})
        made[eid] = cls(merged)

    def usable(eid):
        engine = made.get(eid)
        return bool(engine and engine.ready())

    chosen = config.get("engine") or "auto"
    if chosen in made:
        order = [chosen] + [e for e in FREE_ORDER if e != chosen and usable(e)]
    else:
        order = [e for e in FREE_ORDER if usable(e)]
    if not any(usable(e) for e in order):
        order = [MyMemoryEngine.id]
    return [made[e] for e in dict.fromkeys(order) if e in made and usable(e)] or [made[MyMemoryEngine.id]]
