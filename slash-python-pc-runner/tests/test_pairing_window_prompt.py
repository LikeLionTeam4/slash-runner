"""tray_app.py의 페어링 코드 입력 창 연동 단위 시험 — _pairing_window_command·_prompt_for_pairing_code."""

from unittest.mock import patch

from slash_pc_runner import tray_app


def test_pairing_window_command_dev_mode():
    with patch.object(tray_app, "is_frozen", return_value=False):
        command = tray_app._pairing_window_command(None)

    assert command[1:] == ["-m", "slash_pc_runner.pairing_window"]


def test_pairing_window_command_frozen_mode_with_error():
    with patch.object(tray_app, "is_frozen", return_value=True):
        command = tray_app._pairing_window_command("만료된 코드입니다")

    assert command[1:] == ["--pairing-window", "만료된 코드입니다"]


def test_prompt_for_pairing_code_returns_code_on_success():
    fake_result = type("R", (), {"returncode": 0, "stdout": "123456\n"})()
    with patch.object(tray_app.subprocess, "run", return_value=fake_result):
        assert tray_app._prompt_for_pairing_code() == "123456"


def test_prompt_for_pairing_code_returns_none_on_cancel():
    fake_result = type("R", (), {"returncode": 1, "stdout": ""})()
    with patch.object(tray_app.subprocess, "run", return_value=fake_result):
        assert tray_app._prompt_for_pairing_code() is None


def test_resolve_new_pairing_code_dev_mode_uses_auto_login():
    with patch.object(tray_app, "is_frozen", return_value=False), \
         patch.object(tray_app, "_obtain_pairing_code", return_value="654321") as mock_obtain:
        code = tray_app._resolve_new_pairing_code("http://localhost:4000")

    mock_obtain.assert_called_once_with("http://localhost:4000")
    assert code == "654321"


def test_resolve_new_pairing_code_frozen_mode_prompts_user():
    with patch.object(tray_app, "is_frozen", return_value=True), \
         patch.object(tray_app, "_prompt_for_pairing_code", return_value="111222") as mock_prompt:
        code = tray_app._resolve_new_pairing_code("https://api.dev.sbsh.cloud")

    mock_prompt.assert_called_once_with(None)
    assert code == "111222"


def test_resolve_new_pairing_code_passes_previous_error_to_prompt():
    # register()의 재시도 루프에서 실패 사유를 다음 시도의 창에 보여주기 위해 넘긴다.
    with patch.object(tray_app, "is_frozen", return_value=True), \
         patch.object(tray_app, "_prompt_for_pairing_code", return_value="111222") as mock_prompt:
        tray_app._resolve_new_pairing_code("https://api.dev.sbsh.cloud", "만료된 코드입니다")

    mock_prompt.assert_called_once_with("만료된 코드입니다")


def test_resolve_new_pairing_code_frozen_mode_raises_on_cancel():
    with patch.object(tray_app, "is_frozen", return_value=True), \
         patch.object(tray_app, "_prompt_for_pairing_code", return_value=None):
        try:
            tray_app._resolve_new_pairing_code("https://api.dev.sbsh.cloud")
            assert False, "취소 시 예외가 나야 한다"
        except RuntimeError as e:
            assert "취소" in str(e)
