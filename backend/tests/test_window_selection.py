from dataclasses import replace
import pytest
from sgz_agent.models import WindowSnapshot


def test_selects_explicit_game_title_ignoring_auxiliary_windows():
    from sgz_agent.config import select_window
    main = WindowSnapshot(42, 1, "game.exe", (0, 0, 1280, 720), True, False, "三国志·战略版 电脑模拟器 1709")
    auxiliary = replace(main, hwnd=43, title="", rect=(0, 0, 235, 44))
    assert select_window([auxiliary, main], "^三国志").hwnd == 42


def test_two_matching_game_windows_are_not_silently_selected():
    from sgz_agent.config import select_window
    main = WindowSnapshot(42, 1, "game.exe", (0, 0, 1280, 720), True, False, "三国志")
    with pytest.raises(RuntimeError, match="ambiguous_window"):
        select_window([main, replace(main, hwnd=43)], "^三国志")
