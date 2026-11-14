"""Unit tests for single_instance.py (二重起動防止)"""
import uuid
from unittest.mock import MagicMock

import pytest

from src_backends import single_instance


@pytest.fixture(autouse=True)
def _release_after_test():
    yield
    single_instance.release()


def _unique_name() -> str:
    return f"Local\\Voice2DocTest.{uuid.uuid4().hex}"


class TestAcquire:
    def test_first_acquire_succeeds(self):
        assert single_instance.acquire(_unique_name()) is True

    def test_second_acquire_with_same_name_fails(self):
        """別プロセスの2つ目の起動を模す: 同名ミューテックスが既に存在すれば False"""
        name = _unique_name()
        assert single_instance.acquire(name) is True
        # 同一プロセスでも、別ハンドルでの CreateMutexW は ERROR_ALREADY_EXISTS になる
        assert single_instance._create_mutex(name) is False

    def test_release_allows_reacquire(self):
        name = _unique_name()
        assert single_instance.acquire(name) is True
        single_instance.release()
        assert single_instance.acquire(name) is True

    def test_different_names_do_not_conflict(self):
        assert single_instance.acquire(_unique_name()) is True
        assert single_instance._create_mutex(_unique_name()) is True

    def test_non_windows_or_api_failure_does_not_block_startup(self, monkeypatch):
        """ミューテックスが作れない環境では起動を妨げない（True を返す）"""
        monkeypatch.setattr(single_instance, "_create_mutex", lambda name: (_ for _ in ()).throw(OSError("x")))
        assert single_instance.acquire(_unique_name()) is True


class TestBringExistingToFront:
    def _fake_user32(self, found_titles, iconic=False):
        user32 = MagicMock()
        user32.FindWindowW.side_effect = lambda cls, title: 1234 if title in found_titles else 0
        user32.IsIconic.return_value = 1 if iconic else 0
        return user32

    def test_finds_main_window_and_sets_foreground(self):
        user32 = self._fake_user32({single_instance.MAIN_WINDOW_TITLE})
        assert single_instance.bring_existing_to_front(user32=user32, retries=1, delay=0) is True
        user32.SetForegroundWindow.assert_called_once_with(1234)
        user32.ShowWindow.assert_not_called()

    def test_minimized_window_is_restored_before_foreground(self):
        user32 = self._fake_user32({single_instance.MAIN_WINDOW_TITLE}, iconic=True)
        calls = []
        user32.ShowWindow.side_effect = lambda h, c: calls.append(("show", h, c))
        user32.SetForegroundWindow.side_effect = lambda h: calls.append(("fg", h))
        assert single_instance.bring_existing_to_front(user32=user32, retries=1, delay=0) is True
        assert calls == [("show", 1234, single_instance.SW_RESTORE), ("fg", 1234)]

    def test_falls_back_to_wizard_window(self):
        user32 = self._fake_user32({single_instance.WIZARD_WINDOW_TITLES[1]})
        assert single_instance.bring_existing_to_front(user32=user32, retries=1, delay=0) is True
        user32.SetForegroundWindow.assert_called_once_with(1234)

    def test_returns_false_when_no_window_found_after_retries(self):
        user32 = self._fake_user32(set())
        assert single_instance.bring_existing_to_front(user32=user32, retries=3, delay=0) is False
        assert user32.FindWindowW.call_count >= 3
        user32.SetForegroundWindow.assert_not_called()


def test_names():
    assert single_instance.MUTEX_NAME == r"Local\Voice2Doc.SingleInstance"
    assert single_instance.MAIN_WINDOW_TITLE == "話書き - Voice2Doc"
