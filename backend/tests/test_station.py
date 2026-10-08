"""Outils de l'export de la station (deploy/station), commandes `selftest` et `has-users`."""
import subprocess
import sys
from pathlib import Path

import pytest

STATION = Path(__file__).resolve().parents[2] / "deploy" / "station"
sys.path.insert(0, str(STATION))

import export_kit  # noqa: E402
import lockfile  # noqa: E402


def test_verrou_des_dependances_couvre_l_application():
    found, problems = lockfile.closure(lockfile.read_requirements(Path(__file__).resolve().parents[1] / "requirements.txt"))
    for name in ("fastapi", "surya-ocr", "torch", "sqlalchemy", "python-docx", "cryptography", "platformdirs"):
        assert name in found, name
    assert "pymysql" not in found  # plus de MariaDB
    assert not [p for p in problems if "non installé" in p]


def test_lignes_du_verrou_sans_torch_et_couverture_des_imports():
    lock, torch = export_kit.build_lock(Path(__file__).resolve().parents[1] / "requirements.txt")
    assert set(torch) == {"torch", "torchvision"}
    assert not any(line.lower().startswith(("torch==", "torchvision==")) for line in lock)
    assert all("==" in line for line in lock)


@pytest.mark.parametrize("name,ok", [
    ("colorama-0.4.6-py2.py3-none-any.whl", True),
    ("numpy-2.3.1-cp313-cp313-win_amd64.whl", True),
    ("safetensors-0.8.0-cp310-abi3-win_amd64.whl", True),
    ("torch-2.13.0+cu126-cp313-cp313-win_amd64.whl", True),
    ("numpy-2.3.1-cp313-cp313-manylinux_2_28_x86_64.whl", False),
    ("numpy-2.3.1-cp312-cp312-win_amd64.whl", False),
    ("pywin32-1-cp313-cp313-win32.whl", False),
])
def test_compatibilite_des_roues(name, ok):
    assert export_kit.wheel_compatible(Path(name)) is ok


def test_versions_des_roues_exigees_exactement(tmp_path):
    (tmp_path / "numpy-2.3.0-cp313-cp313-win_amd64.whl").write_bytes(b"x")
    (tmp_path / "torch-2.13.0+cu126-cp313-cp313-win_amd64.whl").write_bytes(b"x")
    have = export_kit.wheel_versions(tmp_path)
    assert "2.3.1" not in have["numpy"] and "2.13.0+cu126" in have["torch"]  # une autre version ne suffit pas


def test_scripts_copies_avec_fins_de_ligne_windows(tmp_path):
    src = tmp_path / "a.bat"
    src.write_bytes(b"@echo off\necho x\r\necho y\n")
    dst = tmp_path / "b.bat"
    export_kit.copy_script(src, dst)
    assert dst.read_bytes() == b"@echo off\r\necho x\r\necho y\r\n"


def test_scripts_bat_sans_piege_cmd():
    """Parenthèse dans un echo de bloc, boucle for /f mal citée : cassent à l'exécution, invisibles à la lecture."""
    res = subprocess.run([sys.executable, str(STATION / "verifier_bat.py")], capture_output=True, text=True)
    assert res.returncode == 0, res.stdout + res.stderr


def test_tous_les_modeles_de_scripts_existent():
    for name in export_kit.SCRIPTS:
        assert (STATION / "templates" / name).is_file(), name
    assert (STATION / "templates" / "appliquer_maj.bat").is_file()


def test_scripts_batch_en_ascii():
    """cmd.exe lit les .bat dans la page de code OEM : un accent y devient du bruit."""
    for f in (STATION / "templates").glob("*.bat"):
        f.read_bytes().decode("ascii")


def test_has_users_et_selftest_du_moteur_factice(client, make_user, capsys):
    from app import cli

    assert cli.main(["has-users"]) == 1
    make_user()
    assert cli.main(["has-users"]) == 0
    assert cli.main(["selftest"]) == 0
    assert "OK" in capsys.readouterr().out


def test_ports_par_defaut_sans_ocr_env(monkeypatch):
    """Démarrer sans ocr_env.bat doit donner les ports de la station, pas les anciens (8000 / 8081)."""
    from app.core import config

    for k in ("OCR_API_PORT", "OCR_FRONTEND_PORT", "OCR_LLAMA_PORT", "OCR_API_HOST", "OCR_LLAMA_HOST"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setitem(config.Settings.model_config, "env_file", None)
    config.reset_settings_cache()
    s = config.get_settings()
    assert (s.api_host, s.api_port, s.frontend_port) == ("0.0.0.0", 8085, 3050)
    assert (s.llama_host, s.llama_port) == ("127.0.0.1", 8091)
    config.reset_settings_cache()


def test_configurer_nettoie_les_anciens_ports(tmp_path, monkeypatch):
    import importlib.util

    spec = importlib.util.spec_from_file_location("configurer", STATION / "templates" / "configurer.py")
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setattr(sys, "argv", ["configurer.py", str(tmp_path)])
    spec.loader.exec_module(mod)
    env = tmp_path / "app" / "backend" / ".env"
    env.parent.mkdir(parents=True)
    key = "k" * 40
    env.write_text(f"OCR_SECRET_KEY={key}\nOCR_API_PORT=8000\nOCR_LLAMA_PORT=8081\nOCR_OLLAMA_URL=http://127.0.0.1:11434\n"
                   "OCR_API_HOST=127.0.0.1\n", encoding="utf-8")
    assert mod.main() == 0
    text = env.read_text(encoding="utf-8")
    assert f"OCR_SECRET_KEY={key}" in text and "OCR_OLLAMA_URL" in text  # le reste est conservé
    assert "PORT" not in text and "OCR_API_HOST" not in text
    env.unlink()
    assert mod.main() == 0 and "OCR_API_PORT" not in env.read_text(encoding="utf-8")  # nouveau .env sans ports
