"""既定の LLM が Gemma 4 E2B であること（定数・画面・文書・必要容量）と、GPU 実行の案内が文書に載っていること"""
import re
from pathlib import Path

from src_backends import config, local_llm, setup_checker

ROOT = Path(__file__).resolve().parent.parent
LABEL = "gemma4:e2b-qat(llama.cpp)"


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8-sig")


def test_default_model_constants():
    assert local_llm.LLAMACPP_MODEL_REPO == "unsloth/gemma-4-E2B-it-qat-GGUF"
    assert local_llm.LLAMACPP_MODEL_FILENAME == "gemma-4-E2B-it-qat-UD-Q4_K_XL.gguf"
    assert local_llm.LLAMACPP_MODEL_SIZE_GB == 2.62
    assert config.DEFAULT_LLM_MODEL_LABEL == LABEL


def test_default_label_is_the_same_on_every_screen_and_script():
    assert LABEL in _read("src/index.html")
    assert f"'{LABEL}'" in _read("src/app.js")
    assert "gemma4:e2b-qat" in _read("main.py")
    assert LABEL in _read("src_backends/test_dialog_pick.py")


def test_no_old_12b_default_is_left_anywhere_public():
    offenders = []
    files = ["README.md", "README.ja.md", "spec.md", "THIRD_PARTY_NOTICES.md", "docs/RELEASING.md",
             "docs/install-guide.md", "docs/install-guide.en.md", "docs/spec/voice2doc.md",
             "src/app.js", "src/i18n.js", "src/index.html", "src/wizard.html",
             "main.py", "src_backends/main.py", "src_backends/config.py", "src_backends/local_llm.py"]
    for name in files:
        for n, line in enumerate(_read(name).splitlines(), 1):
            if re.search(r"12b|gemma-4-12|6\.7 ?GB|6\.72", line, re.I):
                offenders.append(f"{name}:{n}")
    assert offenders == []


def test_wizard_texts_name_e2b_in_both_languages():
    js = _read("src/i18n.js")
    assert js.count("Local LLM (gemma4:e2b-qat)") == 2  # wiz.check_label4 と wz.m011
    assert js.count("ローカル LLM (gemma4:e2b-qat)") == 2
    assert "gemma4:e2b-qat" in _read("src/wizard.html")


def test_disk_requirement_is_7gb_by_default(monkeypatch):
    assert setup_checker.check_disk_space.__defaults__ == (7.0,)

    class Usage:
        free = 7.5 * 1024 ** 3
    monkeypatch.setattr("shutil.disk_usage", lambda p: Usage)
    assert setup_checker.check_disk_space() is True
    Usage.free = 6.5 * 1024 ** 3
    assert setup_checker.check_disk_space() is False


def test_docs_say_2_6gb_and_7gb():
    for name in ("README.md", "README.ja.md", "docs/install-guide.md", "docs/install-guide.en.md"):
        text = _read(name)
        assert re.search(r"2\.6 ?GB", text), name
        assert re.search(r"7 ?GB", text), name


def test_gpu_use_is_documented_everywhere():
    """README・インストールガイド・情報タブ・spec.md に、GPU（Vulkan）の自動利用と CPU への切り替えが書かれている"""
    for name in ("README.md", "docs/install-guide.en.md"):
        text = _read(name)
        assert "Vulkan" in text and "CPU" in text and "driver" in text, name
    for name in ("README.ja.md", "docs/install-guide.md"):
        text = _read(name)
        assert "Vulkan" in text and "CPU" in text and "ドライバー" in text, name
    js = _read("src/i18n.js")
    assert js.count('"about.gpu"') == 2
    assert 'data-i18n="about.gpu"' in _read("src/index.html")
    spec = _read("spec.md")
    for word in ("llm_device", "/llm/status", "llm_gpu_probe", "vulkan-1.dll"):
        assert word in spec, word


def test_model_tab_has_the_device_select():
    html = _read("src/index.html")
    assert re.search(r'<select id="llm-device">\s*<option value="auto"[^>]*>[^<]*</option>\s*'
                     r'<option value="gpu"[^>]*>[^<]*</option>\s*<option value="cpu"', html)
    assert 'id="llm-device-status"' in html
    app = _read("src/app.js")
    assert "llm_device: llmDevice" in app and "/llm/status" in app


def test_vibe_guidance_names_the_file_to_download():
    """Vibe の案内には、ダウンロードするファイル名の例（Intel・AMD の Windows 用）を添え、プレースホルダーは残さない"""
    js = _read("src/i18n.js")
    for key in ("about.prereq_vibe", "wiz.vibe_missing"):
        for text in re.findall(rf'"{re.escape(key)}": "(.*)",\n', js):
            assert "vibe_3.3.2_x64-setup.exe" in text, key
    assert js.count("vibe_3.3.2_x64-setup.exe") == 4  # 日英 × 情報タブ・ウィザード
    for name in ("README.md", "README.ja.md", "docs/install-guide.md", "docs/install-guide.en.md"):
        text = _read(name)
        assert "vibe_3.3.2_x64-setup.exe" in text and "x.y.z" not in text, name
