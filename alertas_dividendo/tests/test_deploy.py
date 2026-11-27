"""Scripts de deploy: agenda congelada, venv que se recria e nenhum segredo no git."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import textwrap
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = Path(__file__).resolve().parents[1]
DEPLOY = PACKAGE / "deploy"
RUN = DEPLOY / "run.sh"
SCHEDULER = DEPLOY / "scheduler.py"
START = DEPLOY / "start_scheduler.sh"
REQUIREMENTS = PACKAGE / "requirements.txt"
DEFAULT_BASE = "/workspace/alertas-dividendo"

PRODUCTION_FREEZE = """\
certifi==2026.11.20
charset-normalizer==3.5.3
idna==3.20
iniconfig==2.3.1
packaging==26.4
pluggy==1.7.0
Pygments==2.21.0
pypdf==7.2.0
pytest==9.2.0
requests==2.34.2
urllib3==2.8.0
"""


def _load_scheduler():
    spec = importlib.util.spec_from_file_location("alertas_deploy_scheduler", SCHEDULER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SCHED = _load_scheduler()


def _at(day, hour, minute, second=0):
    return datetime(2026, 11, day, hour, minute, second, tzinfo=SCHED.TZ)


def _kinds(moment, done=()):
    return [kind for kind, _key in SCHED.slots_due(moment, set(done))]


def test_shell_scripts_pass_bash_n():
    for script in (RUN, START):
        subprocess.run(["bash", "-n", str(script)], check=True)


def test_scheduler_compiles_and_does_not_start_on_import():
    subprocess.run(["python3", "-m", "py_compile", str(SCHEDULER)], check=True)
    assert SCHED.EXIT_SLOTS == [(16, 0), (16, 55), (17, 0), (17, 55)]
    assert SCHED.EXTRA_SLOTS == [("entry-reminder", 9, 0), ("run", 16, 25)]
    assert SCHED.HOURLY_FROM == 8
    assert SCHED.HOURLY_TO == 23
    assert SCHED.SLOT_TOLERANCE.total_seconds() == 30 * 60
    assert SCHED.BASE == os.environ.get("ALERTAS_BASE_DIR", DEFAULT_BASE)
    assert SCHED.RUN == os.path.join(SCHED.BASE, "run.sh")
    assert SCHED.LOG == os.path.join(SCHED.BASE, "scheduler.log")


def test_weekday_slot_table_is_unchanged():
    monday = _at(23, 10, 0)
    friday = _at(27, 10, 0)
    saturday = _at(28, 10, 0)
    sunday = _at(29, 16, 25)
    assert monday.weekday() == 0
    assert friday.weekday() == 4
    assert saturday.weekday() == 5
    assert sunday.weekday() == 6

    assert _kinds(_at(23, 7, 59)) == []
    assert _kinds(_at(23, 8, 0)) == ["run"]
    assert _kinds(_at(23, 23, 59)) == ["run"]
    assert _kinds(_at(24, 0, 0)) == []

    assert _kinds(_at(23, 9, 0)) == ["run", "entry-reminder"]
    assert "entry-reminder" in _kinds(_at(23, 9, 29, 59))
    assert "entry-reminder" not in _kinds(_at(23, 9, 30))

    assert _kinds(_at(23, 16, 0)) == ["run", "exit-reminder"]
    assert _kinds(_at(23, 16, 25)) == ["run", "exit-reminder", "run"]
    keys = [key for _kind, key in SCHED.slots_due(_at(23, 16, 25), set())]
    assert keys == [
        (monday.date(), "run", 16),
        (monday.date(), "exit", 16, 0),
        (monday.date(), "run", 16, 25),
    ]
    assert "exit-reminder" in _kinds(_at(23, 16, 29, 59))
    sixteen_thirty = _kinds(_at(23, 16, 30))
    assert sixteen_thirty.count("exit-reminder") == 0
    assert sixteen_thirty.count("run") == 2

    assert _kinds(_at(23, 16, 55)) == ["run", "exit-reminder"]
    assert [key[2:] for _kind, key in SCHED.slots_due(_at(23, 16, 55), set()) if key[1] == "exit"] == [
        (16, 55)
    ]
    assert _kinds(_at(23, 17, 0)) == ["run", "exit-reminder", "exit-reminder"]
    assert _kinds(_at(23, 17, 55)) == ["run", "exit-reminder"]

    occupied = {
        (monday.date(), "run", 16),
        (monday.date(), "exit", 16, 0),
        (monday.date(), "run", 16, 25),
    }
    assert SCHED.slots_due(_at(23, 16, 25), occupied) == []
    assert _kinds(friday) == ["run"]
    assert _kinds(saturday) == []
    assert _kinds(sunday) == []
    assert SCHED.prune_done(occupied, friday.date()) == set()
    assert SCHED.prune_done(occupied, monday.date()) == occupied


def test_base_dir_is_not_hardcoded_outside_the_default():
    for path in (RUN, SCHEDULER, START):
        for line in path.read_text(encoding="utf-8").splitlines():
            if DEFAULT_BASE in line:
                assert "ALERTAS_BASE_DIR" in line
        assert "TELEGRAM_BOT_TOKEN=" not in path.read_text(encoding="utf-8")
        assert "TELEGRAM_CHAT_ID=" not in path.read_text(encoding="utf-8")
    run = RUN.read_text(encoding="utf-8")
    assert "/usr/bin/flock -w 1200" in run
    assert 'LOCK="$BASE/.lock"' in run
    assert "ALERTAS_SIMULACAO=1" in run
    assert "TZ=America/Sao_Paulo" in run
    assert '. "$BASE/token.env"' in run
    assert '. "$BASE/chat_id.env"' in run
    assert 'VENV="$BASE/venv-alertas"' in run


def test_requirements_match_the_production_freeze():
    pinned = [
        line.strip()
        for line in REQUIREMENTS.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    assert pinned == PRODUCTION_FREEZE.strip().splitlines()


def test_env_files_are_gitignored_and_examples_are_not():
    ignored = [
        "alertas_dividendo/token.env",
        "alertas_dividendo/chat_id.env",
        "alertas_dividendo/deploy/token.env",
        "token.env",
        ".env",
    ]
    for rel in ignored:
        result = subprocess.run(["git", "check-ignore", "-q", rel], cwd=ROOT, check=False)
        assert result.returncode == 0, rel
    for rel in ("alertas_dividendo/.env.example", ".env.example"):
        result = subprocess.run(["git", "check-ignore", "-q", rel], cwd=ROOT, check=False)
        assert result.returncode == 1, rel


def _prepare_base(tmp_path, with_app=True):
    base = tmp_path / "alertas-dividendo"
    base.mkdir()
    (base / "token.env").write_text("TELEGRAM_BOT_TOKEN=token-de-teste\n", encoding="utf-8")
    (base / "chat_id.env").write_text("TELEGRAM_CHAT_ID=chat-de-teste\n", encoding="utf-8")
    if with_app:
        app = base / "app" / "alertas_dividendo"
        app.mkdir(parents=True)
        (app / "requirements.txt").write_text("requests==0\n", encoding="utf-8")
    return base


def _fake_python(bin_dir, marker):
    bin_dir.mkdir(parents=True)
    script = bin_dir / "python3"
    script.write_text(
        textwrap.dedent(
            f"""\
            #!/bin/bash
            marker="${{ALERTAS_TEST_MARKER:?}}"
            if [ "${{1:-}}" = "-m" ] && [ "${{2:-}}" = "venv" ]; then
              if [ -n "${{ALERTAS_VENV_FAIL:-}}" ]; then
                echo "venv create failed" >&2
                exit 1
              fi
              dest=$3
              mkdir -p "$dest/bin"
              cp "{script}" "$dest/bin/python"
              chmod +x "$dest/bin/python"
              cat > "$dest/bin/pip" << 'EOF'
            #!/bin/bash
            if [ -n "${{ALERTAS_PIP_FAIL:-}}" ]; then
              echo "pip install failed" >&2
              exit 1
            fi
            echo "pip" >> "${{ALERTAS_TEST_MARKER:?}}"
            exit 0
            EOF
            chmod +x "$dest/bin/pip"
              echo "venv" >> "$marker"
              if [ -n "${{ALERTAS_VENV_SLEEP:-}}" ]; then
                sleep "$ALERTAS_VENV_SLEEP"
              fi
              exit 0
            fi
            echo "python sim=${{ALERTAS_SIMULACAO-}} tz=${{TZ-}} data=${{ALERTAS_DATA_DIR-}} token=${{TELEGRAM_BOT_TOKEN-}} chat=${{TELEGRAM_CHAT_ID-}} args=$*" >> "$marker"
            exit "${{ALERTAS_PY_EXIT:-0}}"
            """
        ),
        encoding="utf-8",
    )
    script.chmod(0o755)
    return marker


def _run_env(base, marker, **extra):
    env = os.environ.copy()
    bindir = marker.parent / "bin"
    env["PATH"] = f"{bindir}{os.pathsep}/usr/bin{os.pathsep}/bin"
    env["ALERTAS_BASE_DIR"] = str(base)
    env["ALERTAS_TEST_MARKER"] = str(marker)
    env.pop("ALERTAS_VENV_FAIL", None)
    env.pop("ALERTAS_PIP_FAIL", None)
    env.pop("ALERTAS_VENV_SLEEP", None)
    env.pop("ALERTAS_PY_EXIT", None)
    env.update(extra)
    return env


def _marker_lines(marker):
    if not marker.exists():
        return []
    return marker.read_text(encoding="utf-8").splitlines()


def test_run_rebuilds_missing_venv_once_and_exports_the_box_env(tmp_path):
    base = _prepare_base(tmp_path)
    marker = tmp_path / "marker"
    _fake_python(tmp_path / "bin", marker)
    env = _run_env(base, marker)
    first = subprocess.run(["bash", str(RUN), "run"], env=env, check=False)
    second = subprocess.run(["bash", str(RUN), "exit-reminder"], env=env, check=False)
    assert first.returncode == 0
    assert second.returncode == 0
    lines = _marker_lines(marker)
    assert lines.count("venv") == 1
    py = [line for line in lines if line.startswith("python ")]
    assert len(py) == 2
    assert "args=-m alertas_dividendo run" in py[0]
    assert "args=-m alertas_dividendo exit-reminder" in py[1]
    assert "sim=1" in py[0]
    assert "tz=America/Sao_Paulo" in py[0]
    assert f"data={base}/data" in py[0]
    assert "token=token-de-teste" in py[0]
    assert "chat=chat-de-teste" in py[0]
    log = (base / "cron.log").read_text(encoding="utf-8")
    assert log.count("venv ausente, recriando") == 1
    assert (base / ".lock").is_file()
    assert (base / "venv-alertas" / "bin" / "python").is_file()


def test_venv_create_failure_is_logged_and_skips_python(tmp_path):
    base = _prepare_base(tmp_path)
    marker = tmp_path / "marker"
    _fake_python(tmp_path / "bin", marker)
    env = _run_env(base, marker, ALERTAS_VENV_FAIL="1")
    result = subprocess.run(["bash", str(RUN), "run"], env=env, check=False)
    assert result.returncode != 0
    log = (base / "cron.log").read_text(encoding="utf-8")
    assert "falha ao criar o venv" in log
    assert "abortando" in log
    assert not (base / "venv-alertas").exists()
    assert not any(line.startswith("python ") for line in _marker_lines(marker))


def test_pip_failure_removes_the_incomplete_venv(tmp_path):
    base = _prepare_base(tmp_path)
    marker = tmp_path / "marker"
    _fake_python(tmp_path / "bin", marker)
    env = _run_env(base, marker, ALERTAS_PIP_FAIL="1")
    result = subprocess.run(["bash", str(RUN), "run"], env=env, check=False)
    assert result.returncode != 0
    log = (base / "cron.log").read_text(encoding="utf-8")
    assert "falha ao instalar" in log
    assert "sem rodar o python" in log
    assert "venv incompleto removido" in log
    assert not (base / "venv-alertas").exists()
    assert "venv" in _marker_lines(marker)
    assert not any(line.startswith("python ") for line in _marker_lines(marker))


def test_python_exit_code_is_preserved(tmp_path):
    base = _prepare_base(tmp_path)
    marker = tmp_path / "marker"
    _fake_python(tmp_path / "bin", marker)
    env = _run_env(base, marker, ALERTAS_PY_EXIT="4")
    result = subprocess.run(["bash", str(RUN), "run"], env=env, check=False)
    assert result.returncode == 4


def test_missing_app_dir_exits_nonzero(tmp_path):
    base = _prepare_base(tmp_path, with_app=False)
    marker = tmp_path / "marker"
    _fake_python(tmp_path / "bin", marker)
    env = _run_env(base, marker)
    result = subprocess.run(["bash", str(RUN), "run"], env=env, check=False)
    assert result.returncode != 0
    log = (base / "cron.log").read_text(encoding="utf-8")
    assert "pasta do app ausente" in log
    assert not any(line.startswith("python ") for line in _marker_lines(marker))


def test_two_runs_do_not_rebuild_the_venv_at_the_same_time(tmp_path):
    base = _prepare_base(tmp_path)
    marker = tmp_path / "marker"
    _fake_python(tmp_path / "bin", marker)
    env = _run_env(base, marker, ALERTAS_VENV_SLEEP="0.8")
    first = subprocess.Popen(["bash", str(RUN), "run"], env=env)
    second = subprocess.Popen(["bash", str(RUN), "run"], env=env)
    assert first.wait(timeout=30) == 0
    assert second.wait(timeout=30) == 0
    lines = _marker_lines(marker)
    assert lines.count("venv") == 1
    assert sum(line.startswith("python ") for line in lines) == 2


def test_start_scheduler_does_not_start_a_second_copy(tmp_path):
    base = tmp_path / "box"
    base.mkdir()
    bindir = tmp_path / "bin"
    bindir.mkdir()
    marker = tmp_path / "marker"
    pgrep = bindir / "pgrep"
    pgrep.write_text(
        "#!/bin/bash\necho \"pgrep $*\" >> \"$MARKER\"\nexit 0\n",
        encoding="utf-8",
    )
    pgrep.chmod(0o755)
    python3 = bindir / "python3"
    python3.write_text(
        "#!/bin/bash\necho \"python $*\" >> \"$MARKER\"\nexit 0\n",
        encoding="utf-8",
    )
    python3.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{bindir}{os.pathsep}/usr/bin{os.pathsep}/bin"
    env["ALERTAS_BASE_DIR"] = str(base)
    env["MARKER"] = str(marker)
    subprocess.run(["bash", str(START)], env=env, check=False)
    time.sleep(0.4)
    text = marker.read_text(encoding="utf-8")
    assert f"pgrep -f {base}/scheduler.py" in text
    assert "python " not in text


def test_start_scheduler_passes_the_base_dir_to_the_child(tmp_path):
    base = tmp_path / "box"
    base.mkdir()
    bindir = tmp_path / "bin"
    bindir.mkdir()
    marker = tmp_path / "marker"
    pgrep = bindir / "pgrep"
    pgrep.write_text("#!/bin/bash\nexit 1\n", encoding="utf-8")
    pgrep.chmod(0o755)
    python3 = bindir / "python3"
    python3.write_text(
        "#!/bin/bash\necho \"base=${ALERTAS_BASE_DIR-} args=$*\" >> \"$MARKER\"\nexit 0\n",
        encoding="utf-8",
    )
    python3.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{bindir}{os.pathsep}/usr/bin{os.pathsep}/bin"
    env["ALERTAS_BASE_DIR"] = str(base)
    env["MARKER"] = str(marker)
    subprocess.run(["bash", str(START)], env=env, check=False)
    deadline = time.time() + 5
    text = ""
    while time.time() < deadline:
        if marker.exists():
            text = marker.read_text(encoding="utf-8")
            if "args=" in text:
                break
        time.sleep(0.05)
    assert f"base={base}" in text
    assert f"args={base}/scheduler.py" in text
