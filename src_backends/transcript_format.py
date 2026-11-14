"""生の文字起こしテキストの整形（句読点・改行の補完）

vibe-server.exe（whisper.cpp）の出力は1セグメント1行で、句読点がほとんど付かず読みにくい。
LLMによる書き直し（長い録音では数分余計にかかり、言い換え・脱落の恐れもある）は使わず、
ルールだけで次の整形を行う（純関数・決定的・冪等）:

1. 文末表現（です/ます/ました/ください…）で終わる行に「。」、丁寧な疑問（…ですか/ますか）に「？」を補う。
2. 文末でない行（途中で切れたセグメント）は次の行と連結する。
3. 1文ごとに改行し、約 ``PARAGRAPH_MIN_CHARS`` 文字ごとに空行（段落）を入れる。
4. vibe-serverは全文を改行なしの1行で返すことがあるため、**行の途中**でも、既にある「。」「？」「！」と、
   文末表現（です/ます/ました…）の直後で、続く語が接続・終助詞（が・けど・ので・から・し・ね・よ…）でない位置を文の区切りにする。

「、」の挿入は精度が出ないため行わない。語句は一切変更せず、空白・改行と句読点の追加だけを行う。
"""
import re
from typing import List, Optional

# 文末とみなす記号（既にこれで終わっている行は句点を補わない）
SENTENCE_END_CHARS = "。！？!?…」』）).｡"

# 丁寧な疑問の文末 → 「？」を補う（長い語を先に判定する必要は無いが、疑問を先に調べる）
_QUESTION_ENDINGS = ("ですか", "ますか", "でしょうか", "ませんか", "ましたか", "でしたか")

# 丁寧な言い切りの文末 → 「。」を補う
# （「ですが」「ますので」など接続の語尾は文の途中なので含めない）
_STATEMENT_ENDINGS = (
    "です", "ます", "ました", "でした", "ません", "ませんでした", "でしょう",
    "ください", "下さい", "ですね", "ますね", "ですよ", "ますよ", "ましょう",
    "である", "のだ", "んだ", "ありません", "ございます", "ございました",
    "ですよね", "ますよね", "ましたね", "でしたね", "ましたよ", "でしたよ", "でしょうね",
)

# ---- 行の途中の区切り（_split_line） ----
# 文末表現（長い語を先に並べる）。「ますます」の2つ目の「ます」は副詞の一部なので除く
_SPLIT_QUESTIONS = ("ませんか", "ましたか", "でしたか", "でしょうか", "ましょうか", "ですか", "ますか")
_SPLIT_STATEMENTS = (
    "ませんでした", "ございました", "ございます", "ありません", "ですよね", "ますよね", "ましたね", "でしたね",
    "ましたよ", "でしたよ", "でしょうね", "でしょう", "ましょう", "ください", "下さい", "ですね", "ますね", "ですよ",
    "ますよ", "ました", "でした", "ません", "です", "(?<!ます)ます",
)
_END_RE = re.compile(
    r"(?P<punct>[。！？!?｡]+)"
    r"|(?P<question>" + "|".join(_SPLIT_QUESTIONS) + r")"
    r"|(?P<end>" + "|".join(_SPLIT_STATEMENTS) + r")"
)
# 句読点・閉じ括弧が続く位置は区切らない（「ます、」「ます）」など）
_NO_SPLIT_NEXT = "、,，.。！？!?｡…」』）)"
# 文末表現の直後にこれが続くときは文の途中（接続・終助詞・副詞「ますます」・「ますように」等）なので区切らない
_CONTINUATION = re.compile(
    r"(?:か|が|け|ので|のに|の|から|ら|し|ね|ぇ|え|よ|う|と|って|な|で|て|も|は|を|に|ば|さ|わ|ぞ|だ|ま[すしせ])"
)
_CLOSERS = "」』）)"
# 区切った後に残る文字数がこれ未満なら区切らない（「そうですあ」のような断片を作らない）
_MIN_REMAINDER = 2

# 1段落あたりの目安文字数。文の途中では区切らず、この文字数を超えた文末で空行を入れる
PARAGRAPH_MIN_CHARS = 150

# 文末が判定できない行を連結していく長さの上限。これを超えたら元の改行で区切る
# （くだけた話し言葉で全文が1行にならないようにする）
MAX_JOIN_CHARS = 80

_ASCII_WORD_END = re.compile(r"[A-Za-z0-9,;:]$")
_ASCII_WORD_START = re.compile(r"^[A-Za-z0-9]")


def _terminate(line: str) -> Optional[str]:
    """行が文末なら、句読点を補った行を返す。文の途中（連結すべき行）なら None"""
    if line[-1] in SENTENCE_END_CHARS:
        return line
    if line.endswith(_QUESTION_ENDINGS):
        return line + "？"
    if line.endswith(_STATEMENT_ENDINGS):
        return line + "。"
    return None


def _join(buffer: str, line: str) -> str:
    """途中で切れた行を連結する。英数字どうしが接するときだけ空白を入れる"""
    if buffer and _ASCII_WORD_END.search(buffer) and _ASCII_WORD_START.search(line):
        return f"{buffer} {line}"
    return buffer + line


def _split_line(line: str) -> List[str]:
    """1行の途中にある文の区切りで行を分割する。最後の要素以外は句点（または疑問符）で終わる"""
    pieces: List[str] = []
    start = 0
    pos = 0
    while True:
        m = _END_RE.search(line, pos)
        if not m:
            break
        end = m.end()
        terminator = ""
        if m.group("punct"):
            # 「…ます。」と のように閉じ括弧が続くときは括弧まで含め、さらに「と/って」が続くなら文は続いている
            while end < len(line) and line[end] in _CLOSERS:
                end += 1
            tail = line[end:].lstrip()
            if tail.startswith(("と", "って")) and end > m.end():
                pos = end
                continue
        else:
            tail = line[end:].lstrip()
            if tail and (tail[0] in _NO_SPLIT_NEXT or _CONTINUATION.match(tail)):
                pos = end
                continue
            terminator = "？" if m.group("question") else "。"
        if len(tail) < _MIN_REMAINDER:
            break
        pieces.append(line[start:end] + terminator)
        start = end
        while start < len(line) and line[start] == " ":
            start += 1
        pos = start
    pieces.append(line[start:])
    return pieces


def _split_sentences(text: str) -> List[str]:
    sentences: List[str] = []
    buffer = ""
    for raw in text.splitlines():
        line = raw.replace("　", " ").strip()
        if not line:
            continue
        *finished, line = _split_line(line)
        # 行の途中で文が終わっていた分は、直前までのバッファと合わせて1文として確定する
        for piece in finished:
            sentences.append(_join(buffer, piece))
            buffer = ""
        buffer = _join(buffer, line)
        terminated = _terminate(buffer)
        if terminated is not None:
            sentences.append(terminated)
            buffer = ""
        elif len(buffer) >= MAX_JOIN_CHARS:
            sentences.append(buffer)
            buffer = ""
    if buffer:
        sentences.append(buffer)  # 文末が判定できなかった最後の断片はそのまま残す
    return sentences


_JAPANESE_CHAR = re.compile(r"[\u3040-\u30ff\u4e00-\u9fff]")
_JAPANESE_RATIO = 0.1
PARAGRAPH_MIN_CHARS_EN = 400  # 英語は日本語より1文字あたりの情報量が少ないため、段落の長さを長めにする
_EN_END = re.compile(r"[.!?\u2026][\"')\]]*$")
_EN_BOUNDARY = re.compile(r"(?<=[.!?])[\"')\]]*\s+(?=[\"'(\[]?[A-Z0-9])")
# ここで終わる「.」は文末ではない（Mr. Smith / e.g. / p.m. など）
_EN_ABBREVIATIONS = (
    "mr.", "mrs.", "ms.", "dr.", "prof.", "sr.", "jr.", "st.", "vs.", "etc.", "e.g.", "i.e.", "inc.", "ltd.", "no.",
    "a.m.", "p.m.", "u.s.",
)


def is_japanese(text: str) -> bool:
    """日本語の文字起こしか（仮名・漢字が一定の割合以上）。英語など他の言語は別の整形規則を使う"""
    letters = [c for c in text if not c.isspace()]
    if not letters:
        return True
    return sum(1 for c in letters if _JAPANESE_CHAR.match(c)) / len(letters) >= _JAPANESE_RATIO


def _split_sentences_en(text: str) -> List[str]:
    """英語の文ごとに分ける。途中で切れた行は連結し、大文字で始まる次の文の前で改行する"""
    merged: List[str] = []
    buf = ""
    for raw in text.replace("\r\n", "\n").split("\n"):
        line = raw.strip()
        if not line:
            if buf:
                merged.append(buf)
                buf = ""
            continue
        buf = f"{buf} {line}" if buf else line
        if _EN_END.search(buf):
            merged.append(buf)
            buf = ""
    if buf:
        merged.append(buf)

    sentences: List[str] = []
    for segment in merged:
        start = 0
        for m in _EN_BOUNDARY.finditer(segment):
            head = segment[start:m.start()]
            if head.split()[-1].lower() in _EN_ABBREVIATIONS:
                continue
            sentences.append(segment[start:m.end()].strip())
            start = m.end()
        sentences.append(segment[start:].strip())
    sentences = [s for s in sentences if s]
    if sentences and not _EN_END.search(sentences[-1]):
        sentences[-1] += "."
    return sentences


def format_transcript(text: Optional[str]) -> str:
    """文字起こしテキストに句読点と改行を補う。空・None は空文字を返す。
    日本語以外（英語など）の文字起こしは、文末の記号で改行し約400文字ごとに段落を分ける"""
    if not text:
        return ""
    english = not is_japanese(text)
    sentences = _split_sentences_en(text) if english else _split_sentences(text)
    if not sentences:
        return ""
    min_chars = PARAGRAPH_MIN_CHARS_EN if english else PARAGRAPH_MIN_CHARS

    paragraphs: List[List[str]] = []
    current: List[str] = []
    count = 0
    for sentence in sentences:
        current.append(sentence)
        count += len(sentence)
        if count >= min_chars:
            paragraphs.append(current)
            current, count = [], 0
    if current:
        paragraphs.append(current)
    return "\n\n".join("\n".join(p) for p in paragraphs)
