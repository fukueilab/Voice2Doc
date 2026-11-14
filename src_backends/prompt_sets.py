"""言語別の既定プロンプトセット（初期プロンプト）と、表示言語の切替時の入れ替え。

日本語は「文章校正」「企画書」「報告書」「お客様面談記録」。英語はその英訳
（"Proofreading"/"Proposal"/"Report"/"Customer meeting notes"）。既定の選択中プロンプトは文章校正。
表示言語を切り替えたとき、**ユーザーが編集していない**初期プロンプトだけを新しい言語のものへ入れ替える
（編集済みの内容は変更しない）。音声認識の言語も、既定値のままなら言語に合わせて入れ替える。
"""
import copy
from typing import Dict, Optional

from .i18n import normalize_language

_PROOFREAD_JA = (
    "# 役割\nあなたはプロの編集者兼校正者です。提供されたテキストの校正を行ってください。\n\n"
    "# 指示\n- テキストの文章を校正し、読みやすい文章に整えてください。\n"
    "- 要約は絶対にせず、元の文章の情報や内容はすべて残してください。\n"
    "- 誤字脱字、文法的な誤り、意味の通らない箇所（不自然な表現）を修正してください。\n"
    "- 出力は校正された文章のみとし、挨拶、前置き、"
    "および何をどのように校正したかの説明などは一切記載しないでください。\n"
    "- 文章の1行目には、内容を端的に表すタイトルをマークダウンのH1（#）で付加してください。\n"
    "- 文章の意味のかたまりごとに章を分け、それぞれの章タイトルをマークダウンのH2（##）で付加してください。\n"
)
_PROOFREAD_EN = (
    "# Role\nYou are a professional editor and proofreader. Proofread the text provided.\n\n"
    "# Instructions\n- Proofread the text and make it easy to read.\n"
    "- Never summarize. Keep all of the information and content of the original text.\n"
    "- Fix typos, grammatical errors and passages that do not make sense (unnatural expressions).\n"
    "- Output only the proofread text. Do not write any greeting, preface or explanation of what was "
    "corrected or how.\n"
    "- On the first line, add a title that briefly describes the content as a Markdown H1 (#).\n"
    "- Divide the text into chapters by meaning and add each chapter title as a Markdown H2 (##).\n"
)

_PROPOSAL_JA = (
    "# 役割\nあなたは優秀なビジネスプランナー兼プロの編集者です。提供されたテキストファイルの内容を分析し、"
    "社内稟議やクライアント提案でそのまま使える、論理的で説得力のある「企画書」を作成してください。\n\n"
    "# 目的\n提供された素材（テキスト）の情報を整理・構造化し、読み手が短時間でプロジェクトの価値、実現性、"
    "メリットを理解できる完成度の高い企画書を出力すること。\n\n"
    "# 企画書の構成要件\n企画書だけ作成してください。挨拶文や作成理由など、以下1～8の構成ではない文章は不要です。\n"
    "以下の構成に沿って企画書を作成してください。テキスト内に情報がない項目については、"
    "一般的なビジネスの文脈を想定して適切に補完・推測し、その旨を注記してください。\n\n"
    "1. **企画タイトル**（キャッチーかつ一目で内容が伝わるもの）\n"
    "2. **背景・課題設定**（なぜ今この企画が必要なのか、どんな課題を解決するのか）\n"
    "3. **企画概要**（どのような施策・プロジェクトなのかの全体像）\n"
    "4. **ターゲット・ペルソナ**（誰に向けたものか）\n"
    "5. **主なベネフィット（提供価値）**（顧客や社内にとってのメリット）\n"
    "6. **実施内容・スケジュール**（具体的なアクションプランと大まかな流れ）\n"
    "7. **期待される成果・KPI**（どのような効果が見込めるか）\n"
    "8. **懸念点とリスク対策**（想定される課題と、その対策）\n\n"
    "# トーン＆マナー\n- 構造化された見出しや箇条書きを多用し、視認性を高くすること。\n"
    "- ビジネスパーソンとして信頼感を与える、簡潔でプロフェッショナルなトーンを使用すること。"
)
_PROPOSAL_EN = (
    "# Role\nYou are a skilled business planner and professional editor. Analyze the content of the text "
    "provided and write a logical, persuasive proposal that can be used as is for an internal approval "
    "request or a client pitch.\n\n"
    "# Purpose\nOrganize and structure the information in the material (text) provided, and output a "
    "polished proposal from which readers can quickly understand the value, feasibility and benefits of "
    "the project.\n\n"
    "# Structure of the proposal\nWrite only the proposal. Do not write greetings, the reason for writing "
    "it or any other text outside items 1 to 8 below.\n"
    "Write the proposal following the structure below. For items with no information in the text, "
    "fill them in appropriately by assuming a typical business context, and note that you did so.\n\n"
    "1. **Proposal title** (catchy, and conveys the content at a glance)\n"
    "2. **Background and issues** (why this proposal is needed now and which issue it solves)\n"
    "3. **Overview** (the overall picture of the measure or project)\n"
    "4. **Target and persona** (who it is for)\n"
    "5. **Main benefits (value provided)** (the merits for customers or for the company)\n"
    "6. **Activities and schedule** (concrete action plan and rough timeline)\n"
    "7. **Expected results and KPIs** (what effects can be expected)\n"
    "8. **Concerns and risk measures** (expected issues and how to address them)\n\n"
    "# Tone and manner\n- Use structured headings and bullet points generously to make it easy to scan.\n"
    "- Use a concise, professional tone that gives a sense of trust as a businessperson."
)

_REPORT_JA = (
    "# 役割\nあなたは優秀なビジネスパーソンです。提供されたテキストの内容をベースに、"
    "上司へ提出するための論理的で分かりやすい報告書を作成してください。\n\n"
    "# 指示\n- 報告内容と口調は上司に報告することを意識する事\n"
    "- 提供されたテキストの文章を校正し、誤字脱字、文法的な誤り、意味の通らない箇所を修正して、"
    "ビジネスに適した読みやすい文章に整えてください。\n"
    "- 元のテキストに含まれる情報や意味を損なわずに報告書として構成してください。\n"
    "- 出力は生成された報告書のみとし、前置き、後書き、および校正や作成プロセスの説明などは一切記載しないでください。\n"
    "- 1行目には報告書全体のタイトルをマークダウンのH1（#）で記載してください。\n"
    "- 各報告事項の見出しにはマークダウンのH2（##）を使用してください。\n"
)
_REPORT_EN = (
    "# Role\nYou are a capable businessperson. Based on the text provided, write a logical, easy-to-understand "
    "report to submit to your manager.\n\n"
    "# Instructions\n- Keep in mind that the content and tone are for reporting to your manager.\n"
    "- Proofread the text provided, fix typos, grammatical errors and passages that do not make sense, and "
    "make it easy to read and suitable for business.\n"
    "- Structure it as a report without losing the information or meaning of the original text.\n"
    "- Output only the generated report. Do not write any preface, afterword or explanation of the "
    "proofreading or writing process.\n"
    "- On the first line, write the title of the whole report as a Markdown H1 (#).\n"
    "- Use a Markdown H2 (##) for the heading of each reported item.\n"
)

_MEETING_JA = (
    "#命令\n-以下の整理項目で整理してください\n-年月日は音声ファイル名から推察してください\n"
    "-お客様名、部署名、面談者が音声ファイルになかった場合はブランクにしてください\n"
    "-件名が音声ファイルになかった場合、内容から件名を生成してください\n\n\n"
    "#整理項目\n-年月日\n-お客様名\n-部署名\n-面談者\n-件名\n-箇条書きで要約\n-問題点\n-課題点\n-ToDo\n-ネクストアクション"
)
_MEETING_EN = (
    "#Instructions\n-Organize the content under the items below\n-Infer the date from the audio file name\n"
    "-Leave the customer name, department and interviewee blank if they are not in the audio\n"
    "-If there is no subject in the audio, generate one from the content\n\n\n"
    "#Items\n-Date\n-Customer name\n-Department\n-Interviewee\n-Subject\n-Summary in bullet points\n"
    "-Problems\n-Issues\n-To-do\n-Next actions"
)

# 初期プロンプトの識別子（並び順がそのまま画面の並び順。先頭が既定の選択中プロンプト）
BUILTIN_IDS = ("proofread", "proposal", "report", "meeting")

PROMPT_SETS: Dict[str, Dict[str, dict]] = {
    "ja": {
        "proofread": {"name": "文章校正", "content": _PROOFREAD_JA,
                      "description": "文章の誤字脱字・文法を修正し、読みやすい文章に整えます"},
        "proposal": {"name": "企画書", "content": _PROPOSAL_JA,
                     "description": (
                         "企画タイトル、背景・課題設定、企画概要、ターゲット・ペルソナ、主なベネフィット、"
                         "実施内容・スケジュール、期待される成果・KPI、懸念点とリスク対策、をまとめます"
                     )},
        "report": {"name": "報告書", "content": _REPORT_JA,
                   "description": (
                       "文章を校正し、誤字脱字、文法的な誤り、意味の通らない箇所を修正して、上司への報告文章を生成します"
                   )},
        "meeting": {"name": "お客様面談記録", "content": _MEETING_JA,
                    "description": "お客様との会話を問題点、課題点ToDo等で面談記録として要約します"},
    },
    "en": {
        "proofread": {"name": "Proofreading", "content": _PROOFREAD_EN,
                      "description": "Fixes typos and grammar and polishes the text so it is easy to read"},
        "proposal": {"name": "Proposal", "content": _PROPOSAL_EN,
                     "description": (
                         "Organizes the title, background and issues, overview, target and persona, main benefits, "
                         "activities and schedule, expected results and KPIs, and concerns and risk measures"
                     )},
        "report": {"name": "Report", "content": _REPORT_EN,
                   "description": (
                       "Proofreads the text, fixes typos, grammatical errors and unclear passages, "
                       "and writes a report for your manager"
                   )},
        "meeting": {"name": "Customer meeting notes", "content": _MEETING_EN,
                    "description": (
                        "Summarizes a conversation with a customer as meeting notes with problems, issues, "
                        "to-dos and more"
                    )},
    },
}

DEFAULT_PROMPT_ID = BUILTIN_IDS[0]

# 音声認識の言語の既定値（"auto" は言語の自動検出）
DEFAULT_WHISPER_LANGUAGE = {"ja": "ja", "en": "auto"}


def default_whisper_language(lang: str) -> str:
    return DEFAULT_WHISPER_LANGUAGE[normalize_language(lang)]


def recognition_language(value: Optional[str]) -> Optional[str]:
    """設定の音声認識言語を、認識エンジンへ渡す言語コードにする（"auto"・空は None=自動検出）"""
    value = (value or "").strip()
    return None if value in ("", "auto") else value


def default_prompt(lang: str) -> str:
    """既定の選択中プロンプト（文章校正 / Proofreading）の本文"""
    return PROMPT_SETS[normalize_language(lang)][DEFAULT_PROMPT_ID]["content"]


def builtin_names(lang: Optional[str] = None) -> tuple:
    """初期プロンプトの名前。lang を省略すると、すべての言語の名前（削除不可の判定用）"""
    if lang is not None:
        return tuple(PROMPT_SETS[normalize_language(lang)][i]["name"] for i in BUILTIN_IDS)
    names = [s[i]["name"] for s in PROMPT_SETS.values() for i in BUILTIN_IDS]
    return tuple(dict.fromkeys(names))


def default_active_prompt_name(lang: str) -> str:
    return PROMPT_SETS[normalize_language(lang)][DEFAULT_PROMPT_ID]["name"]


def default_saved_prompts(lang: str) -> dict:
    """言語の初期プロンプト一式 {名前: {content, description, output_dir, include_transcript, hidden}}"""
    out = {}
    for pid in BUILTIN_IDS:
        entry = PROMPT_SETS[normalize_language(lang)][pid]
        out[entry["name"]] = {
            "content": entry["content"],
            "description": entry["description"],
            "output_dir": "",
            "include_transcript": False,
            "hidden": False,
        }
    return out


def switch_language(config, new_lang: str) -> None:
    """設定の表示言語を new_lang に切り替え、**編集していない**初期プロンプトと既定の音声認識言語を入れ替える。

    編集済みのプロンプト（内容が初期値と違うもの）と、ユーザーが選んだ音声認識言語は変更しない。"""
    new_lang = normalize_language(new_lang)
    old_lang = normalize_language(config.ui_language)
    if new_lang == old_lang:
        return
    prompts = dict(config.saved_prompts or {})
    for pid in BUILTIN_IDS:
        old, new = PROMPT_SETS[old_lang][pid], PROMPT_SETS[new_lang][pid]
        current = prompts.get(old["name"])
        if not isinstance(current, dict) or current.get("content") != old["content"]:
            continue  # 無い・編集済みなら触らない
        if new["name"] in prompts:
            continue  # 切替先の名前を利用者が既に使っている場合は、そのプロンプトを上書きしない
        del prompts[old["name"]]
        replaced = copy.deepcopy(current)
        replaced["content"] = new["content"]
        if current.get("description") == old["description"]:
            replaced["description"] = new["description"]
        prompts[new["name"]] = replaced
        if config.active_prompt_name == old["name"]:
            config.active_prompt_name = new["name"]
            config.prompt = new["content"]
    config.saved_prompts = prompts
    if config.whisper_language == default_whisper_language(old_lang):
        config.whisper_language = default_whisper_language(new_lang)
    config.ui_language = new_lang
