"""フロントエンド(src/)の静的検査

画面を実際に開かずに、HTML/CSS/JS の取りこぼし（消し忘れたID・使っていない配色・UI部品）を検出する。
実画面の確認はブラウザで別途行う。"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"

HTML = (SRC / "index.html").read_text(encoding="utf-8-sig")
CSS = (SRC / "style.css").read_text(encoding="utf-8-sig")
JS = (SRC / "app.js").read_text(encoding="utf-8-sig")
WIZARD_HTML = (SRC / "wizard.html").read_text(encoding="utf-8-sig")
WIZARD_CSS = (SRC / "wizard.css").read_text(encoding="utf-8-sig")
WIZARD_JS = (SRC / "wizard.js").read_text(encoding="utf-8-sig")

OLD_ACCENTS = ("#e94560", "#c73e54", "rgba(233, 69, 96", "rgba(233,69,96", "#ff6b81", "#fff5f7")
NEW_ACCENT = "#6c4cf1"


def _html_ids(html: str) -> set:
    # HTMLコメント内のid="..."は実在しないので除く
    return set(re.findall(r'\bid="([^"]+)"', re.sub(r"<!--.*?-->", "", html, flags=re.S)))


def _js_referenced_ids(js: str) -> set:
    ids = set(re.findall(r"getElementById\(\s*['\"]([^'\"]+)['\"]\s*\)", js))
    ids |= set(re.findall(r"querySelector\(\s*['\"]#([\w-]+)['\"]\s*\)", js))
    return ids


class TestIdIntegrity:
    # テンプレートリテラル等で動的に作られる/実行時に生成されるIDは除外する
    DYNAMIC = {"download-overlay"}  # app.js が実行時に作る要素

    def test_every_id_referenced_by_app_js_exists_in_index_html(self):
        missing = sorted(_js_referenced_ids(JS) - _html_ids(HTML) - self.DYNAMIC)
        assert missing == [], f"app.js が参照するIDが index.html に無い: {missing}"

    def test_every_id_referenced_by_wizard_js_exists_in_wizard_html(self):
        missing = sorted(_js_referenced_ids(WIZARD_JS) - _html_ids(WIZARD_HTML))
        assert missing == [], f"wizard.js が参照するIDが wizard.html に無い: {missing}"

    def test_ids_are_unique(self):
        ids = re.findall(r'\bid="([^"]+)"', HTML)
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        assert dupes == []


class TestHeaderNavigation:
    def test_sidebar_is_removed(self):
        assert 'class="sidebar"' not in HTML and "<nav class=\"sidebar" not in HTML
        assert ".sidebar {" not in CSS and ".sidebar{" not in CSS

    def test_header_has_three_pages_in_order(self):
        header = re.search(r"<header[^>]*class=\"app-header\"[^>]*>.*?</header>", HTML, re.S)
        assert header, "header.app-header が無い"
        pages = re.findall(r'class="menu-item[^"]*"[^>]*data-page="([^"]+)"', header.group(0))
        assert pages == ["file-input", "history", "settings"]

    def test_version_element_lives_in_header(self):
        header = re.search(r"<header[^>]*class=\"app-header\"[^>]*>.*?</header>", HTML, re.S).group(0)
        assert 'class="version"' in header
        assert ".sidebar-footer" not in JS  # 旧セレクタが残っていない

    def test_each_nav_target_has_a_page(self):
        for page in ("file-input", "history", "settings"):
            assert f'id="page-{page}"' in HTML

    def test_window_title_is_unchanged_for_single_instance_lookup(self):
        """二重起動時の前面化は、ウィンドウタイトルでFindWindowする"""
        assert "<title>話書き - Voice2Doc</title>" in HTML  # 翻訳対象にしない（ウィンドウ検索に使うため）
        from src_backends.single_instance import MAIN_WINDOW_TITLE
        assert MAIN_WINDOW_TITLE == "話書き - Voice2Doc"


class TestDesignTokens:
    @pytest.mark.parametrize("old", OLD_ACCENTS)
    def test_old_red_accent_is_gone_from_main_css(self, old):
        assert old not in CSS.lower(), f"旧アクセント色 {old} が style.css に残っている"

    @pytest.mark.parametrize("old", OLD_ACCENTS)
    def test_old_red_accent_is_gone_from_wizard_css(self, old):
        assert old not in WIZARD_CSS.lower()

    def test_new_accent_is_defined_as_primary(self):
        root = re.search(r":root\s*\{(.*?)\}", CSS, re.S).group(1)
        assert re.search(r"--primary:\s*#6c4cf1\s*;", root, re.I)

    REQUIRED_VARS = ("--primary", "--primary-hover", "--primary-light", "--bg-main", "--bg-card", "--bg-secondary",
                     "--border", "--text-primary", "--text-secondary", "--success", "--danger", "--warning")

    def test_root_and_dark_define_required_variables(self):
        root = re.search(r":root\s*\{(.*?)\}", CSS, re.S).group(1)
        dark = re.search(r'\[data-theme="dark"\]\s*\{(.*?)\}', CSS, re.S).group(1)
        for var in self.REQUIRED_VARS:
            assert f"{var}:" in root, f":root に {var} が無い"
        for var in ("--primary", "--primary-hover", "--primary-light", "--bg-main", "--bg-card", "--border",
                    "--text-primary"):
            assert f"{var}:" in dark, f'[data-theme="dark"] に {var} が無い'

    def test_sidebar_variables_are_gone(self):
        assert "--sidebar" not in CSS

    def test_font_stack_is_system_ui(self):
        assert '"Yu Gothic UI"' in CSS and "Meiryo" in CSS

    def test_primary_matches_icon_color(self):
        icon = (ROOT / "scripts" / "make_icon.py").read_text(encoding="utf-8-sig")
        r, g, b = (int(NEW_ACCENT[i:i + 2], 16) for i in (1, 3, 5))
        assert re.search(rf"PRIMARY\s*=\s*\(\s*{r}\s*,\s*{g}\s*,\s*{b}\s*,\s*255\s*\)", icon)


class TestSettingsFooter:
    def _settings(self) -> str:
        return re.search(r'<div class="page" id="page-settings">.*?(?=\n    <!-- |\Z)', HTML, re.S).group(0)

    def test_old_per_tab_save_and_reset_buttons_are_gone(self):
        for old in ("btn-save-prompt", "btn-save-model", "btn-reset-prompt", "btn-reset-model"):
            assert f'id="{old}"' not in HTML, f"{old} が残っている"

    def test_footer_bar_has_reset_and_save(self):
        footer = re.search(r'<div class="footer-bar"[^>]*>.*?</div>\s*</div>|<div class="footer-bar"[^>]*>.*?</div>',
                           self._settings(), re.S)
        assert footer, ".footer-bar が設定ページに無い"
        assert 'id="btn-footer-reset"' in footer.group(0) and 'id="btn-footer-save"' in footer.group(0)

    def test_tabs_support_dirty_marker(self):
        assert ".tab-btn.dirty::after" in CSS
        assert "dirty" in JS

    def test_prompt_autosave_is_removed(self):
        assert "debounceAutoSave" not in JS

    def test_ctrl_s_saves_current_tab(self):
        assert "btn-save-model').click()" not in JS.replace('"', "'")


class TestProgressOverlay:
    def test_progress_container_is_an_overlay_with_cancel(self):
        m = re.search(r'<div class="[^"]*progress-overlay[^"]*" id="progress-container"[^>]*>\s*'
                      r'<div class="progress-dialog"', HTML)
        assert m, "#progress-container が .progress-overlay（中央ダイアログ）になっていない"
        assert 'id="btn-cancel-process"' in HTML

    def test_progress_elements_keep_their_ids(self):
        for i in ("progress-bar", "progress-text", "progress-status", "progress-detail", "audio-duration",
                  "streaming-output", "streaming-text"):
            assert f'id="{i}"' in HTML


class TestDashboard:
    def test_lists_and_banner_exist(self):
        for i in ("recent-results-list", "recent-errors-list", "status-banner"):
            assert f'id="{i}"' in HTML, f"{i} が無い"

    def test_step_cards_are_removed(self):
        """「音声認識/AI生成/保存」の3項目表示は不要（表示しない）"""
        assert "step-card" not in HTML and "step-cards" not in CSS and "step-card" not in JS
        assert "setStepCard" not in JS and "renderStepCards" not in JS


class TestHistoryScreen:
    def test_row_shows_file_name_and_meta_without_title_line(self):
        """出力名は「元ファイル名-H1タイトル」なので、タイトル行を別に出すと重複する"""
        assert "history-title" not in JS and ".history-title" not in CSS
        assert "titleHtml" not in JS

    def test_rows_have_checkbox_and_no_per_row_delete_buttons(self):
        assert 'class="history-check"' in JS
        assert 'data-action="hide"' not in JS and 'data-action="delete-file"' not in JS

    def test_bulk_buttons_sit_next_to_clear_all_in_actions_bar(self):
        bar = re.search(r'<div class="history-actions" id="history-actions".*?</div>', HTML, re.S).group(0)
        order = [bar.index(i) for i in ('id="btn-clear-history"', 'id="btn-hide-selected"', 'id="btn-delete-selected"')]
        assert order == sorted(order), "全履歴削除 → 履歴削除 → ファイル削除 の順に並べる"
        assert 'id="history-select-all"' in bar

    def test_bulk_actions_use_ids_api_and_confirm_dialog(self):
        assert "/history/hide" in JS and "/history/delete-files" in JS
        assert "askConfirm(" in JS
        assert "confirm(" not in re.sub(r"askConfirm\(|askDiscardSettings\(", "", JS)  # ブラウザ標準のconfirmは使わない

    def test_confirm_dialog_has_yes_and_cancel(self):
        modal = re.search(
            r'<div class="modal-overlay" id="confirm-modal".*?</div>\s*</div>\s*</div>', HTML, re.S).group(0)
        assert "キャンセル" in modal and ">はい<" in modal
        assert 'id="btn-confirm-ok"' in modal and 'id="btn-confirm-cancel"' in modal

    def test_attributes_with_ids_are_escaped(self):
        assert "escapeAttr(item.id)" in JS and "escapeHtml(item.name)" in JS


class TestSettingsTabName:
    def test_appearance_tab_is_renamed_to_kankyo_settings(self):
        assert '>環境設定</button>' in HTML
        assert '>外観</button>' not in HTML
        assert 'data-tab="appearance-settings"' in HTML  # 内部のIDは維持（JS・保存動作への影響を避ける）


class TestCancelUi:
    def test_cancel_is_handled_by_shared_run_state(self):
        for needle in ("function beginRun", "function cancelCurrentRun", "function finishCancelled",
                       "controller.abort()", "signal: run.controller.signal", "status === 'cancelled'"):
            assert needle in JS, needle

    def test_all_three_flows_register_a_run(self):
        assert JS.count("beginRun(") >= 4  # 定義1 + ファイル/テキスト/リトライ

    def test_abort_is_not_retried_by_api_request(self):
        assert "e.name === 'AbortError'" in JS

    def test_cancel_button_only_calls_shared_cancel(self):
        assert "getElementById('btn-cancel-process').addEventListener('click', () => cancelCurrentRun())" in JS


class TestWizardStyle:
    def test_wizard_uses_light_purple_theme(self):
        assert NEW_ACCENT in WIZARD_CSS.lower()
        assert "#1a1a2e" not in WIZARD_CSS.lower()


class TestStructure:
    def test_shared_modals_are_outside_every_page(self):
        """ページ(.page)の中のモーダルは、そのページが非表示だと開いても見えない（開始画面の一覧から履歴詳細を開く等）"""
        from html.parser import HTMLParser

        class Walker(HTMLParser):
            def __init__(self):
                super().__init__()
                self.stack = []
                self.inside_page = {}

            def handle_starttag(self, tag, attrs):
                if tag in ("meta", "link", "input", "br", "img", "hr"):
                    return
                a = dict(attrs)
                if a.get("id") in ("history-modal", "settings-leave-modal", "reset-confirm-modal", "confirm-modal",
                                   "processing-nav-confirm-modal", "vibe-fallback-modal"):
                    self.inside_page[a["id"]] = any(c and "page" in c.split() for c in self.stack)
                self.stack.append(a.get("class"))

            def handle_endtag(self, tag):
                if self.stack and tag not in ("meta", "link", "input", "br", "img", "hr"):
                    self.stack.pop()

        w = Walker()
        w.feed(HTML)
        assert w.inside_page, "モーダルが見つからない"
        assert not any(w.inside_page.values()), f"ページ内に置かれたモーダル: {w.inside_page}"

    def test_html_tags_are_balanced(self):
        from html.parser import HTMLParser

        class P(HTMLParser):
            def __init__(self):
                super().__init__()
                self.stack, self.errors = [], []

            def handle_starttag(self, tag, attrs):
                if tag not in ("meta", "link", "input", "br", "img", "hr"):
                    self.stack.append(tag)

            def handle_endtag(self, tag):
                if self.stack and self.stack[-1] == tag:
                    self.stack.pop()
                else:
                    self.errors.append(tag)

        p = P()
        p.feed(HTML)
        assert p.errors == [] and p.stack == []

    def test_dynamic_ids_built_in_js_exist_in_html(self):
        """テンプレートリテラルで組み立てるIDは getElementById の静的検査では拾えないので個別に確認する"""
        ids = _html_ids(HTML)
        for page in ("file-input", "history", "settings"):
            assert f"page-{page}" in ids
        for tab in ("prompt-settings", "model-settings", "appearance-settings"):
            assert f"tab-{tab}" in ids
        for mode in ("file", "text"):
            assert f"mode-{mode}" in ids

    def test_header_is_above_progress_overlay_and_below_modals(self):
        z = lambda sel: int(re.search(re.escape(sel) + r"\s*\{[^}]*?z-index:\s*(\d+)", CSS, re.S).group(1))  # noqa: E731
        assert z(".progress-overlay") < z(".app-header") < z(".modal-overlay")

    def test_result_container_precedes_dashboard_lists(self):
        assert HTML.index('id="result-container"') < HTML.index('id="dashboard-lists"')

    def test_unsaved_guard_hooks_exist(self):
        for needle in ("askDiscardSettings", "hasDirty()", "setDirty('prompt-settings', false)",
                       "if (onSettings && targetPage === 'settings') return;"):
            assert needle in JS, needle
