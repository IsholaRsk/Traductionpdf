"""TradFilez — lecture et reconstruction des fichiers.

Chaque format renvoie une liste d'« unités » (chaînes d'une seule ligne) à
traduire, puis reconstruit le fichier à partir des traductions en conservant
sa structure : numérotation et horodatages des sous-titres, clés JSON,
en-têtes CSV, blocs de code Markdown, styles Word, etc.

`units[i] is None`  → unité volontairement laissée telle quelle (code, horodatage…).
"""

from __future__ import annotations

import io
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
VENDOR = os.path.join(HERE, "vendor")
if os.path.isdir(VENDOR) and VENDOR not in sys.path:
    sys.path.insert(0, VENDOR)

try:  # facultatif : améliore la fidélité HTML
    from bs4 import BeautifulSoup  # type: ignore

    HAS_BS4 = True
except Exception:  # noqa: BLE001
    HAS_BS4 = False


class Unsupported(Exception):
    """Format non géré ou fichier illisible."""


def flat(text):
    """Une unité = une ligne : on aplati les retours à la ligne internes."""
    return " ".join(p.strip() for p in (text or "").replace("\r", "").split("\n") if p.strip())


def squeeze(text):
    return re.sub(r"\s+", " ", text or "").strip()


TECH_VALUE = re.compile(
    r"""^(?:
        https?://\S+ | ftp://\S+ | mailto:\S+ | tel:\S+ | data:\S+
      | /[\w./\-]* | \.{1,2}/[\w./\-]* | ~[\w./\-]*
      | [\w.\-+/]+@[\w.\-]+
      | [\d_]+(?:[.,:]\d+)*%?
      | 0x[0-9a-fA-F]+
      | (?:true|false|null|none|nan|yes|no|on|off|undefined)
      | [A-Za-z0-9]+(?:[._\-/+][A-Za-z0-9]+)+
      | \S+\.(?:png|jpe?g|gif|svg|webp|bmp|ico|css|js|jsx|ts|json|ya?ml|toml|ini|md|html?|xml|pdf|zip|tar|gz|mp4|webm|txt|csv|docx?|xlsx?)
    )$""",
    re.I | re.X,
)
# codes de langue ou de région : uniquement en majuscules, pour ne pas effacer
# « Un », « le », « yes » saisis comme du texte ordinaire
ISO_CODE = re.compile(r"^[A-Z]{2,3}(?:-[A-Za-z]{2,4})?$")


TECH_KEYS = frozenset("""
slug id ids uuid uid guid url uri href src dest path file filename folder image img icon avatar
lang language locale code locale_key type kind class classname style stylesheet version revision build
date datetime timestamp created updated modified expiry author owner editor email phone tel fax
password secret token apikey api_key key hash md5 sha1 sha256 etag signature sku ref reference parent child
anchor alias selector encoding charset mimetype mime media_type color colour background font weight
size width height x y z index order rank priority position start end from to limit offset count sum
percent currency timezone theme layout template partial section region grid row col column span
display align justify opacity margin padding border radius shadow duration delay interval repeat
visible enabled disabled hidden required readonly nullable default unit units flag switch toggle
""".split())


def key_is_technical(key):
    """Vrai pour les clés de données qui ne se traduisent pas (identifiants, chemins, styles…)."""
    k = re.sub(r"^['\"]+|['\"]+$", "", str(key).strip()).lower().replace("-", "_")
    if not k:
        return True
    return (
        k in TECH_KEYS
        or k.endswith(("_id", "_ids", "_url", "_uri", "_path", "_key", "_hash", "_at", "_code", "_type"))
        or k.startswith(("http_", "data-", "data_", "aria-", "x-", "_"))
    )


def is_technical(value):
    """Vrai pour les identifiants, chemins, nombres, codes langue… qui ne se traduisent pas."""
    v = (value or "").strip()
    if not v:
        return True
    if len(v) > 44 or re.search(r"\s", v):
        return False
    return bool(TECH_VALUE.match(v) or ISO_CODE.match(v))


def sniff_delimiter(text):
    """Déduit le séparateur d'un CSV en ignorant ce qui est entre guillemets."""
    sample = "\n".join(text.split("\n")[:60])
    stripped = re.sub(r'"(?:[^"]|"")*"', '""', sample)
    best, best_score = ",", -1.0
    for cand in (";", ",", "\t", "|"):
        counts = [ln.count(cand) for ln in stripped.split("\n") if ln.strip()]
        nonzero = [c for c in counts if c > 0]
        if not nonzero:
            continue
        consistency = len(nonzero) / max(len(counts), 1)
        score = min(nonzero) * (1.0 if consistency == 1.0 else 0.45) + 0.1 * sum(nonzero) / len(nonzero) * consistency
        if score > best_score:
            best, best_score = cand, score
    return best



class Doc:
    kind = "txt"
    label = "Texte"
    note = ""

    def __init__(self, name):
        self.name = name
        self.units: list[str | None] = []
        self.original: list[str] = []

    def add(self, text, guard=False):
        """Enregistre une unité à traduire.

        `guard=True` (formats structurés) laisse tels quels les jetons
        techniques : identifiants, chemins, URL, nombres, codes langue…
        """
        value = flat(text)
        if not value or (guard and is_technical(value)):
            self.original.append(value or (text or ""))
            self.units.append(None)
            return len(self.units) - 1
        self.original.append(value)
        self.units.append(value)
        return len(self.units) - 1

    def keep(self, text=""):
        """Unité non traduite (code, horodatage, ligne vide…)."""
        self.original.append(text or "")
        self.units.append(None)

    @property
    def unit_count(self):
        return len(self.units)

    @property
    def todo(self):
        return [i for i, u in enumerate(self.units) if u]

    def preview_in(self):
        return "\n".join(s for s in self.original if s and s.strip())

    def out_name(self, suffix="_traduit"):
        root, ext = os.path.splitext(self.name)
        return f"{root}{suffix}{ext}"

    def rebuild(self, translations):  # pragma: no cover - interface
        raise NotImplementedError


# --------------------------------------------------------------------------- #
# découpage commun
# --------------------------------------------------------------------------- #


def paragraphs_of(lines):
    """Groupe les lignes en paragraphes ; les lignes vides deviennent des blocs vides."""
    blocks, current = [], []
    for ln in lines:
        if ln.strip() == "":
            if current:
                blocks.append(current)
                current = []
            blocks.append([])
        else:
            current.append(ln)
    if current:
        blocks.append(current)
    return blocks


def rewrap(text, width):
    """Recoupe un paragraphe à la largeur d'origine (pour les .txt « durs »)."""
    if not text or width <= 12 or len(text) < 40:
        return text
    out, cur = [], ""
    for word in text.split():
        if cur and len(cur) + 1 + len(word) > width:
            out.append(cur)
            cur = word
        else:
            cur = f"{cur} {word}".strip()
    if cur:
        out.append(cur)
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# texte / Markdown / RST
# --------------------------------------------------------------------------- #


class TextDoc(Doc):
    kind = "txt"
    label = "Texte"

    def __init__(self, name, text, keep_lines=False, wrap=True):
        super().__init__(name)
        self.text = text
        self.keep_lines = keep_lines
        self.wrap = wrap
        self.blocks = []
        lines = text.replace("\r", "").split("\n")
        if keep_lines:
            for ln in lines:
                self.blocks.append(("line", ln))
                self.add(ln)
        else:
            for blk in paragraphs_of(lines):
                joined = " ".join(x.strip() for x in blk).strip()
                width = max((len(x) for x in blk), default=0)
                self.blocks.append(("para", joined, width))
                self.add(joined)
        self.width = max((len(ln) for ln in lines), default=0)

    def rebuild(self, translations):
        parts = []
        for block, tr in zip(self.blocks, translations):
            if block[0] == "line":
                parts.append(tr if (tr or "").strip() else block[1])
                continue
            _, joined, width = block
            if not joined:
                parts.append("")
            else:
                parts.append(rewrap((tr or joined).strip(), width) if self.wrap else (tr or joined).strip())
        if self.keep_lines:
            body = "\n".join(parts)
        else:
            body = "\n\n".join(p for p in parts if p != "") if all(
                p == "" for p in parts
            ) else "\n\n".join(parts)
            if re.search(r"\n{3,}", body):
                body = re.sub(r"\n{3,}", "\n\n", body)
            if self.text.endswith("\n") and not body.endswith("\n"):
                body += "\n"
        return body.encode("utf-8"), self.out_name("_traduit"), self.preview_in(), body


class MarkdownDoc(Doc):
    kind = "md"
    label = "Markdown"
    note = "Blocs de code et formules laissés tels quels."

    def __init__(self, name, text, keep_lines=False):
        super().__init__(name)
        self.text = text
        self.keep_lines = keep_lines
        self.blocks = []
        segments = re.split(r"(```.*?```|~~~.*?~~~)", text.replace("\r", ""), flags=re.S)
        for i, seg in enumerate(segments):
            if i % 2 == 1:  # bloc de code
                self.blocks.append(("code", seg))
                self.keep()
                continue
            if keep_lines:
                for ln in seg.split("\n"):
                    self.blocks.append(("line", ln))
                    self.add(ln)
            else:
                for blk in paragraphs_of(seg.split("\n")):
                    joined = " ".join(x.strip() for x in blk).strip()
                    self.blocks.append(("para", joined))
                    if joined:
                        self.add(joined)
                    else:
                        self.keep()

    def rebuild(self, translations):
        pieces, in_code = [], False
        for block, tr in zip(self.blocks, translations):
            if block[0] == "code":
                pieces.append(block[1])
                continue
            if block[0] == "line":
                pieces.append(tr if (tr or "").strip() else block[1])
                continue
            joined = block[1]
            if not joined:
                pieces.append("")
            else:
                pieces.append((tr or joined).strip())
        body = "\n\n".join(p for p in pieces) if not self.keep_lines else "\n".join(pieces)
        if self.keep_lines:
            body = re.sub(r"(```\n?)\n{2,}", r"\1", body)
        else:
            body = re.sub(r"\n{3,}", "\n\n", body)
        if self.text.endswith("\n") and not body.endswith("\n"):
            body += "\n"
        return body.encode("utf-8"), self.out_name("_traduit"), self.preview_in(), body


# --------------------------------------------------------------------------- #
# sous-titres
# --------------------------------------------------------------------------- #


class SubtitleDoc(Doc):
    kind = "srt"
    label = "Sous-titres"
    note = "Numérotation, horodatages et en-têtes conservés."

    def __init__(self, name, text, is_vtt=False):
        super().__init__(name)
        self.is_vtt = is_vtt
        self.text = text
        self.cues = []
        lines = text.replace("\r", "").split("\n")
        head, body = [], []
        for i, ln in enumerate(lines):
            if i == 0 and is_vtt and ln.strip().upper().startswith("WEBVTT"):
                self.cues.append(("raw", [ln]))
                continue
            if ln.strip() == "":
                if body:
                    self._flush(head, body)
                head, body = [], []
                self.cues.append(("raw", [""]))
                continue
            if is_vtt and ln.strip().upper().startswith(("NOTE", "STYLE", "REGION")):
                self.cues.append(("raw", [ln]))
                continue
            if re.fullmatch(r"\d+", ln.strip()) or "-->" in ln:
                if body:
                    self._flush(head, body)
                    head, body = [], []
                head.append(ln)
            else:
                body.append(ln)
        if body:
            self._flush(head, body)
        if not self.todo:
            raise Unsupported("Aucun texte de sous-titre trouvé dans ce fichier.")

    def _flush(self, head, body_lines):
        joined = " ".join(x.strip() for x in body_lines if x.strip())
        idx = self.add(joined)
        self.cues.append(("cue", list(head), idx, joined))

    def rebuild(self, translations):
        out = []
        for cue in self.cues:
            if cue[0] == "raw":
                out.extend(cue[1])
                continue
            _, head, idx, fallback = cue
            out.extend(head)
            text = (translations[idx] or "").strip() or fallback
            if text:
                out.append(text)
        body = "\n".join(out)
        body = re.sub(r"\n{3,}", "\n\n", body)
        if self.text.endswith("\n") and not body.endswith("\n"):
            body += "\n"
        return body.encode("utf-8"), self.out_name("_traduit"), self.preview_in(), body


# --------------------------------------------------------------------------- #
# données structurées
# --------------------------------------------------------------------------- #


class JsonDoc(Doc):
    kind = "json"
    label = "JSON"
    note = "Structure, clés et types intacts — seules les valeurs texte sont traduites."

    def __init__(self, name, text, keys=None):
        super().__init__(name)
        self.raw = text
        try:
            self.data = json.loads(text)
        except Exception as exc:  # noqa: BLE001
            raise Unsupported(f"JSON invalide : {exc}") from exc
        self.keys_filter = set(keys or [])
        self.indent = 2 if "\n" in text else None
        self.paths = []

        def walk(node, path):
            if isinstance(node, dict):
                for k, v in node.items():
                    walk(v, path + [k])
            elif isinstance(node, list):
                for i, v in enumerate(node):
                    walk(v, path + [i])
            elif isinstance(node, str) and node.strip():
                if self.keys_filter and not (set(str(p) for p in path) & self.keys_filter):
                    return
                if key_is_technical(path[-1] if path else ""):
                    return
                for line_no, line in enumerate(node.replace("\r", "").split("\n")):
                    self.paths.append((tuple(path), line_no))
                    self.add(line, guard=True)

        walk(self.data, [])
        if not self.todo:
            raise Unsupported("Aucune chaîne à traduire dans ce JSON.")

    def rebuild(self, translations):
        data = json.loads(json.dumps(self.data))
        for (path, line_no), tr in zip(self.paths, translations):
            if not (tr or "").strip():
                continue
            cur = data
            for p in path[:-1]:
                cur = cur[p]
            key = path[-1]
            lines = str(cur[key]).replace("\r", "").split("\n")
            while len(lines) <= line_no:
                lines.append("")
            lines[line_no] = tr.strip()
            cur[key] = "\n".join(lines)
        body = json.dumps(data, ensure_ascii=False, indent=self.indent)
        return body.encode("utf-8"), self.out_name("_traduit"), self.preview_in(), body


class CsvDoc(Doc):
    kind = "csv"
    label = "Tableau CSV"
    note = "Séparateur détecté automatiquement ; en-têtes non traduits par défaut."

    def __init__(self, name, text, translate_header=False, delimiter=None):
        super().__init__(name)
        import csv

        self._csv = csv
        delim = delimiter or sniff_delimiter(text)
        self.delimiter = delim

        class _Dialect(csv.Dialect):
            delimiter = delim
            quotechar = '"'
            doublequote = True
            skipinitialspace = False
            lineterminator = "\n"
            quoting = csv.QUOTE_MINIMAL

        self.dialect = _Dialect
        self.rows = [r for r in csv.reader(io.StringIO(text.replace("\r\n", "\n")), self.dialect) if r or True]
        if not self.rows:
            raise Unsupported("Tableau vide.")
        self.cells = []
        for r, row in enumerate(self.rows):
            for c, cell in enumerate(row):
                if r == 0 and not translate_header:
                    continue
                if not cell.strip():
                    continue
                for line_no, line in enumerate(cell.split("\n")):
                    self.cells.append((r, c, line_no))
                    self.add(line, guard=True)
        if not self.todo:
            raise Unsupported("Aucune cellule textuelle à traduire.")

    def rebuild(self, translations):
        rows = [list(r) for r in self.rows]
        for (r, c, line_no), tr in zip(self.cells, translations):
            if not (tr or "").strip():
                continue
            parts = rows[r][c].split("\n")
            while len(parts) <= line_no:
                parts.append("")
            parts[line_no] = tr.strip()
            rows[r][c] = "\n".join(parts)
        out = io.StringIO()
        writer = self._csv.writer(out, self.dialect)
        writer.writerows(rows)
        body = out.getvalue()
        return body.encode("utf-8"), self.out_name("_traduit"), self.preview_in(), body


class HtmlDoc(Doc):
    kind = "html"
    label = "HTML"
    note = "Balises, attributs, scripts et styles intacts."

    def __init__(self, name, text):
        super().__init__(name)
        self.text = text
        self.spans = []
        if HAS_BS4:
            self.soup = BeautifulSoup(text, "html.parser")
            self.nodes = []
            for node in self.soup.find_all(string=True):
                if type(node).__name__ != "NavigableString":
                    # NavigableString seuls : on laisse doctype, commentaires, CDATA, styles…
                    continue
                parent = node.parent.name if node.parent else ""
                if parent in ("script", "style", "code", "pre", "textarea"):
                    continue
                plain = squeeze(str(node))
                if not plain:
                    continue
                self.nodes.append(node)
                self.add(plain)
            if not self.todo:
                raise Unsupported("Aucun texte visible à traduire dans ce HTML.")
        else:
            for m in re.finditer(r">([^<>]+)<", text):
                plain = squeeze(m.group(1))
                if not plain:
                    continue
                self.spans.append((m.start(1), m.end(1), plain))
                self.add(plain)
            if not self.todo:
                raise Unsupported("Aucun texte visible à traduire dans ce HTML.")

    def rebuild(self, translations):
        if HAS_BS4:
            for node, tr in zip(self.nodes, translations):
                if (tr or "").strip():
                    node.replace_with(tr.strip())
            body = str(self.soup)
        else:
            out, last = [], 0
            for (s, e, plain), tr in zip(self.spans, translations):
                out.append(self.text[last:s])
                out.append((tr or plain).strip())
                last = e
            out.append(self.text[last:])
            body = "".join(out)
        return body.encode("utf-8"), self.out_name("_traduit"), self.preview_in(), body


class ConfigDoc(Doc):
    """YAML / TOML / INI : on ne traduit que la valeur après « clé : »."""

    kind = "config"
    label = "Fichier de configuration"
    note = "Clés, indentation et guillemets conservés."

    LINE = re.compile(r"^(\s*(?:-\s+)?)([\w.\-\[\]\"']+)(\s*[:=]\s*)(.*?)(\s*)$")

    def __init__(self, name, text):
        super().__init__(name)
        self.text = text
        self.lines = []
        for ln in text.replace("\r", "").split("\n"):
            m = self.LINE.match(ln)
            if m and m.group(4).strip() and not m.group(4).lstrip().startswith(("#", "//")) and not key_is_technical(m.group(2)):
                value = m.group(4).strip()
                quote = ""
                if value[:1] in ("'", '"') and value[-1:] == value[:1] and len(value) > 1:
                    quote, value = value[0], value[1:-1]
                self.lines.append(("kv", m.group(1) + m.group(2) + m.group(3), quote, ln))
                self.add(value, guard=True)
                continue
            item = re.match(r"^(\s*-\s+)(.*)$", ln)
            if item and item.group(2).strip() and ":" not in item.group(2).split(" ")[0]:
                self.lines.append(("kv", item.group(1), "", ln))
                self.add(item.group(2).strip(), guard=True)
            else:
                self.lines.append(("raw", ln))
                self.keep(ln)
        if not self.todo:
            raise Unsupported("Aucune valeur textuelle à traduire.")

    def rebuild(self, translations):
        out = []
        for index, (spec, tr) in enumerate(zip(self.lines, translations)):
            if spec[0] == "raw":
                out.append(spec[1])
                continue
            _, prefix, quote, raw_line = spec
            value = (tr or "").strip()
            if not value:
                out.append(raw_line if self.units[index] is None else f"{prefix.rstrip()} {quote}{quote}")
            else:
                out.append(f"{prefix}{quote}{value}{quote}".rstrip())
        body = "\n".join(out)
        if self.text.endswith("\n") and not body.endswith("\n"):
            body += "\n"
        return body.encode("utf-8"), self.out_name("_traduit"), self.preview_in(), body


class PoDoc(Doc):
    kind = "po"
    label = "Catalogue Gettext"
    note = "msgstr traduits, msgid et en-tête technique conservés."

    MSGSTR = re.compile(r'^msgstr\s+"((?:[^"\\]|\\.)*)"\s*$')
    CONT = re.compile(r'^"(?P<v>(?:[^"\\]|\\.)*)"\s*$')

    def __init__(self, name, text):
        super().__init__(name)
        self.text = text
        self.lines = text.replace("\r", "").split("\n")
        self.entries = []  # (start, end, is_header)
        i = 0
        while i < len(self.lines):
            m = self.MSGSTR.match(self.lines[i])
            if not m:
                i += 1
                continue
            parts = [m.group(1)]
            j = i + 1
            while j < len(self.lines):
                c = self.CONT.match(self.lines[j])
                if not c:
                    break
                parts.append(c.group("v"))
                j += 1
            raw = "".join(parts)
            decoded = raw.replace('\\"', '"').replace("\\n", " ").replace("\\t", " ").strip()
            is_header = "Project-Id-Version" in decoded or not decoded
            self.entries.append((i, j, is_header))
            if is_header:
                self.keep()
            else:
                self.add(decoded)
            i = j
        if not self.todo:
            raise Unsupported("Aucune chaîne msgstr à traduire dans ce catalogue.")

    @staticmethod
    def _encode(text):
        return text.replace("\\", "\\\\").replace('"', '\\"')

    def rebuild(self, translations):
        lines = list(self.lines)
        for (start, end, is_header), tr in zip(self.entries, translations):
            if is_header:
                continue
            value = self._encode((tr or "").strip())
            lines[start] = f'msgstr "{value}"'
            for k in range(start + 1, end):
                lines[k] = '""'
        body = "\n".join(lines)
        if self.text.endswith("\n") and not body.endswith("\n"):
            body += "\n"
        return body.encode("utf-8"), self.out_name("_traduit"), self.preview_in(), body


# --------------------------------------------------------------------------- #
# formats binaires
# --------------------------------------------------------------------------- #


class OfficeDoc(Doc):
    kind = "docx"
    label = "Document Word"
    note = "Styles de paragraphe conservés ; le formatage intra-paragraphe est simplifié."

    def __init__(self, name, blob):
        super().__init__(name)
        try:
            import docx  # type: ignore
        except Exception as exc:  # noqa: BLE001
            raise Unsupported("python-docx est indisponible sur ce serveur.") from exc
        try:
            self.document = docx.Document(io.BytesIO(blob))
        except Exception as exc:  # noqa: BLE001
            raise Unsupported(f"Document Word illisible : {exc}") from exc
        self.targets = []

        def collect(paragraphs):
            for p in paragraphs:
                txt = squeeze(p.text)
                if not txt:
                    continue
                self.targets.append(p)
                self.add(txt)

        collect(self.document.paragraphs)
        for table in self.document.tables:
            for row in table.rows:
                for cell in row.cells:
                    collect(cell.paragraphs)
        for section in self.document.sections:
            for part in (section.header, section.footer):
                try:
                    collect(part.paragraphs)
                except Exception:  # noqa: BLE001
                    pass
        if not self.targets:
            raise Unsupported("Aucun paragraphe texte dans ce document.")

    def rebuild(self, translations):
        for p, tr in zip(self.targets, translations):
            text = (tr or "").strip()
            if not text:
                continue
            runs = [r for r in p.runs if r.text and r.text.strip()]
            if not runs:
                p.add_run(text)
                continue
            runs[0].text = text
            for r in runs[1:]:
                r.text = ""
        out = io.BytesIO()
        self.document.save(out)
        body = "\n\n".join(t for t in translations if (t or "").strip())
        return out.getvalue(), self.out_name("_traduit"), self.preview_in(), body


class XlsxDoc(Doc):
    kind = "xlsx"
    label = "Classeur Excel"
    note = "Formules, nombres, mises en forme et feuilles conservés."

    def __init__(self, name, blob):
        super().__init__(name)
        try:
            import openpyxl  # type: ignore
        except Exception as exc:  # noqa: BLE001
            raise Unsupported("openpyxl est indisponible sur ce serveur.") from exc
        try:
            self.wb = openpyxl.load_workbook(io.BytesIO(blob))
        except Exception as exc:  # noqa: BLE001
            raise Unsupported(f"Classeur illisible : {exc}") from exc
        self.cells = []
        for ws in self.wb.worksheets:
            for row in ws.iter_rows():
                for cell in row:
                    v = cell.value
                    if isinstance(v, str) and v.strip() and not v.startswith("="):
                        self.cells.append(cell)
                        self.add(squeeze(v), guard=True)
        if not self.todo:
            raise Unsupported("Aucune cellule textuelle à traduire.")

    def rebuild(self, translations):
        for cell, tr in zip(self.cells, translations):
            if (tr or "").strip():
                cell.value = tr.strip()
        out = io.BytesIO()
        self.wb.save(out)
        body = "\n\n".join(t for t in translations if (t or "").strip())
        return out.getvalue(), self.out_name("_traduit"), self.preview_in(), body


def _mupdf():
    """PyMuPDF, si installé : c'est lui qui permet de rendre un vrai PDF et non un texte."""
    try:
        import pymupdf
        return pymupdf
    except Exception:  # noqa: BLE001
        try:
            import fitz as pymupdf  # ancien nom du module
            return pymupdf
        except Exception:  # noqa: BLE001
            return None


def _droitier(texte):
    """Le bloc est-il écrit de droite à gauche (arabe, hébreu…) ?"""
    marques = sum(1 for c in texte if "\u0590" <= c <= "\u08ff" or "\ufb1d" <= c <= "\ufeff")
    return marques * 2 > max(len(texte), 1)


def _echapper(texte):
    return (texte or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class PdfDoc(Doc):
    """Traduction d'un PDF en PDF : même pagination, même empreinte de texte.

    Le principe, celui des outils de traduction de documents : on *efface* chaque
    bloc de texte d'origine (annotation de caviardage, images et tracés épargnés),
    puis on repose sa traduction dans le rectangle exact du bloc, corps de police
    d'origine et repli automatique si la langue prend plus de place.

    Sans PyMuPDF, on retombe sur l'extraction texte seule — et on le dit.
    """

    kind = "pdf"
    label = "PDF"
    note = ""

    def __init__(self, name, blob):
        super().__init__(name)
        m = _mupdf()
        if m is None:
            self._texte_seul(blob)
            return
        try:
            doc = m.open(stream=blob, filetype="pdf")
        except Exception as exc:  # noqa: BLE001
            raise Unsupported(f"PDF illisible : {exc}") from exc
        try:
            if doc.needs_pass and not doc.authenticate(""):
                raise Unsupported("PDF protégé par un mot de passe : lecture impossible.")
            self.source = blob
            self.blocs = []
            for pno, page in enumerate(doc):
                for block in page.get_text("dict")["blocks"]:
                    if block.get("type") != 0 or not block.get("lines"):
                        continue
                    x0, y0, x1, y1 = block["bbox"]
                    if x1 - x0 < 3 or y1 - y0 < 3:
                        continue
                    spans = [sp for ln in block["lines"] for sp in ln.get("spans", ())]
                    if not spans:
                        continue
                    texte = " ".join((sp.get("text") or "").strip() for sp in spans).strip()
                    if not texte:
                        continue
                    idx = self.add(texte)
                    self.blocs.append({
                        "u": idx, "p": pno, "r": (x0, y0, x1, y1),
                        "t": max(5.0, min(max((sp.get("size") or 10) for sp in spans), 44.0)),
                        "c": int(spans[0].get("color") or 0),
                        # attributs de la police du premier span : graisse, italique,
                        # famille — collés à l’original (flags PyMuPDF)
                        "f": int(spans[0].get("flags") or 0),
                    })
        except Unsupported:
            raise
        except Exception as exc:  # noqa: BLE001
            raise Unsupported(f"PDF illisible : {exc}") from exc
        finally:
            doc.close()
        if not self.todo:
            raise Unsupported("Aucun texte sélectionnable dans ce PDF (peut être un scan).")
        self.label = "PDF"
        self.note = (
            "PDF reconstruit page à page : le texte d’origine est effacé, sa traduction reposée "
            "dans son rectangle — pagination, images, tracés, corps et graisse de police, et mise "
            "en page conservés. Les polices sont substituées (Helvetica, Times, Courrier) : le "
            "crénage peut légèrement bouger, le texte d’origine a disparu."
        )

    def _texte_seul(self, blob):
        """Repli sans PyMuPDF : on lit le texte avec pypdf, la sortie est un fichier texte."""
        try:
            from pypdf import PdfReader  # type: ignore
        except Exception as exc:  # noqa: BLE001
            raise Unsupported(
                "Extraction PDF indisponible sur ce serveur. Copiez le texte du PDF dans l'onglet « Texte »."
            ) from exc
        self.blocs = []
        self.source = blob
        try:
            reader = PdfReader(io.BytesIO(blob))
            if reader.is_encrypted:
                try:
                    reader.decrypt("")
                except Exception:  # noqa: BLE001
                    raise Unsupported("PDF chiffré : lecture impossible.") from None
            pages = [(p.extract_text() or "") for p in reader.pages]
        except Unsupported:
            raise
        except Exception as exc:  # noqa: BLE001
            raise Unsupported(f"PDF illisible : {exc}") from exc
        body = "\n\n".join(p.strip() for p in pages if p.strip())
        if not body.strip():
            raise Unsupported("Aucun texte sélectionnable dans ce PDF (peut être un scan).")
        self.text = body
        for blk in paragraphs_of(body.split("\n")):
            self.add(" ".join(x.strip() for x in blk).strip())
        if not self.todo:
            raise Unsupported("Aucun texte sélectionnable dans ce PDF.")
        self.label = "PDF (texte extrait)"
        self.note = ("PyMuPDF n'est pas installé sur ce serveur : seul le texte est rendu, "
                     "en fichier .txt. Installez-le (pip install pymupdf) pour conserver la mise en page.")

    def rebuild(self, translations):
        if not getattr(self, "blocs", None):
            body = "\n\n".join(t for t in translations if (t or "").strip())
            nom = self.out_name("").rsplit(".", 1)[0] + "_traduit.txt"
            return body.encode("utf-8"), nom, self.preview_in(), body

        m = _mupdf()
        doc = m.open(stream=self.source, filetype="pdf")
        par_page = {}
        for bloc in self.blocs:
            par_page.setdefault(bloc["p"], []).append(bloc)
        for pno, blocs in par_page.items():
            page = doc[pno]
            # un bloc sans traduction ne doit pas laisser de trou : on ne le touche pas
            a_remplacer = [b for b in blocs if (translations[b["u"]] or "").strip()]
            if not a_remplacer:
                continue
            for bloc in a_remplacer:
                page.add_redact_annot(m.Rect(bloc["r"]))
            page.apply_redactions(images=m.PDF_REDACT_IMAGE_NONE, graphics=m.PDF_REDACT_LINE_ART_NONE)
            for bloc in a_remplacer:
                texte = (translations[bloc["u"]] or "").strip()
                x0, y0, x1, y1 = bloc["r"]
                rect = m.Rect(x0, y0, max(x0 + 8, x1), max(y0 + 6, y1 + 2))
                c = bloc["c"]
                couleur = "#%02x%02x%02x" % ((c >> 16) & 255, (c >> 8) & 255, c & 255)
                sens = " direction:rtl;text-align:right;" if _droitier(texte) else ""
                f = bloc.get("f") or 0
                gras, italique = f & 16, f & 2
                famille = "monospace" if f & 8 else ("serif" if f & 4 else "sans-serif")
                html = (f'<div style="font-size:{bloc["t"]:.1f}pt;color:{couleur};'
                        f'font-family:{famille};{"font-weight:bold;" if gras else ""}'
                        f'{"font-style:italic;" if italique else ""}'
                        f'line-height:1.24;{sens}">{_echapper(texte)}</div>')
                try:
                    page.insert_htmlbox(rect, html, scale_low=0.2)
                except Exception:  # noqa: BLE001 - un bloc récalcitrant ne doit pas perdre le fichier
                    try:
                        page.insert_text((x0, y1 - 1), texte[:400], fontsize=bloc["t"],
                                         fontname=("hebo" if gras else "helv"),
                                         color=[v / 255 for v in ((c >> 16) & 255, (c >> 8) & 255, c & 255)])
                    except Exception:  # noqa: BLE001
                        pass
        try:  # ne garder que les glyphes utilisés : le fichier reste léger
            doc.subset_fonts()
        except Exception:  # noqa: BLE001
            pass
        out = doc.tobytes(deflate=True, garbage=4)
        nom = self.out_name("_traduit")
        return out, nom, self.preview_in(), "\n\n".join(t for t in translations if (t or "").strip())


# --------------------------------------------------------------------------- #
# point d'entrée
# --------------------------------------------------------------------------- #

EXT_KIND = {
    ".txt": "txt", ".text": "txt", ".log": "txt", ".asc": "txt",
    ".md": "md", ".markdown": "md", ".mdown": "md", ".rst": "md",
    ".srt": "srt", ".sub": "srt", ".vtt": "vtt",
    ".json": "json", ".jsonl": "json", ".geojson": "json",
    ".csv": "csv", ".tsv": "csv",
    ".html": "html", ".htm": "html",
    ".yaml": "config", ".yml": "config", ".toml": "config", ".ini": "config", ".cfg": "config",
    ".po": "po",
    ".docx": "docx", ".xlsx": "xlsx", ".xlsm": "xlsx", ".pdf": "pdf",
}

REFUSAL = (
    "Format non pris en charge pour l'instant. Enregistrez en .docx, .txt, .md, .pdf, "
    ".csv, .json ou .srt — ou collez le texte dans l'onglet « Texte »."
)


def load(filename, blob, options=None):
    options = options or {}
    name = os.path.basename(filename or "document.txt")
    ext = os.path.splitext(name)[1].lower()
    kind = EXT_KIND.get(ext)

    if kind == "docx":
        return OfficeDoc(name, blob)
    if kind in ("xlsx",):
        return XlsxDoc(name, blob)
    if kind == "pdf" or (not ext and blob[:4] == b"%PDF"):
        return PdfDoc(name, blob)
    if kind is None and ext in (".doc", ".odt", ".rtf", ".pptx", ".ppt", ".epub", ".key", ".pages", ".xls"):
        raise Unsupported(REFUSAL)
    if blob[:4] == b"%PDF":
        return PdfDoc(name, blob)
    if kind is None and blob[:2] == b"PK":
        raise Unsupported(REFUSAL)
    if b"\x00" in blob[:2048]:
        raise Unsupported("Fichier binaire non pris en charge.")

    try:
        text = blob.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = None
        for enc in ("cp1252", "latin-1", "utf-16"):
            try:
                text = blob.decode(enc)
                break
            except Exception:  # noqa: BLE001
                continue
        if text is None:
            raise Unsupported("Encodage non reconnu : enregistrez le fichier en UTF-8.")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if not text.strip():
        raise Unsupported("Fichier vide.")

    if kind == "md":
        return MarkdownDoc(name, text, keep_lines=bool(options.get("keep_lines")))
    if kind in ("srt", "vtt"):
        return SubtitleDoc(name, text, is_vtt=kind == "vtt")
    if kind == "json":
        if ext == ".jsonl":
            return TextDoc(name, text, keep_lines=True, wrap=False)
        return JsonDoc(name, text, keys=options.get("json_keys"))
    if kind == "csv":
        return CsvDoc(
            name,
            text,
            translate_header=bool(options.get("translate_header")),
            delimiter="\t" if ext == ".tsv" else None,
        )
    if kind == "html":
        return HtmlDoc(name, text)
    if kind == "config":
        return ConfigDoc(name, text)
    if kind == "po":
        return PoDoc(name, text)
    return TextDoc(name, text, keep_lines=bool(options.get("keep_lines")), wrap=not options.get("no_wrap"))
