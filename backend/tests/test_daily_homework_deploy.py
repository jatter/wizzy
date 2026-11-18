"""Generate deployment scripts without contacting or mutating a server."""
import json
import subprocess
from pathlib import Path


def test_cloud_environment_transfers_optional_vision_binding(tmp_path):
    source = Path(__file__).resolve().parents[2] / 'build-deploy.sh'
    definitions = source.read_text().split('MODE="${1:-}"', 1)[0]
    project = tmp_path / 'project'
    project.mkdir()
    assets = project / '.local/android-release/.well-known'
    assets.mkdir(parents=True)
    (assets / 'assetlinks.json').write_text(json.dumps([{'target': {'sha256_cert_fingerprints': ['00:' * 31 + '00']}}]))
    required = ['POSTGRES_PASSWORD', 'WIZZY_APP_ROLE', 'WIZZY_APP_PASSWORD', 'WIZZY_DISPATCHER_ROLE',
                'WIZZY_DISPATCHER_PASSWORD', 'WIZZY_WORKER_ROLE', 'WIZZY_WORKER_PASSWORD']
    base = '\n'.join(key + '=synthetic-test-value' for key in required) + '\n'
    runner = tmp_path / 'environment.sh'
    runner.write_text(definitions + '\nPROJECT_DIR="$1"\nwrite_cloud_db_env "$2"\n')
    destination = tmp_path / 'cloud.env'

    def generate(value):
        (project / '.env').write_text(base + value)
        return subprocess.run(['bash', str(runner), str(project), str(destination)], capture_output=True, text=True)

    model = '458af0aa-88b0-46cf-8840-832d6c8ceb46'
    assert generate('WIZZY_HOMEWORK_LIST_MODEL_ID=' + model + '\n').returncode == 0
    assert 'WIZZY_HOMEWORK_LIST_MODEL_ID=' + model in destination.read_text()
    assert generate('').returncode == 0
    assert 'WIZZY_HOMEWORK_LIST_MODEL_ID' not in destination.read_text()
    assert generate('WIZZY_HOMEWORK_LIST_MODEL_ID=not-a-model-id\n').returncode != 0


def test_generated_scripts_manage_independent_import_worker(tmp_path):
    source = Path(__file__).resolve().parents[2] / 'build-deploy.sh'
    definitions = source.read_text().split('MODE="${1:-}"', 1)[0]
    runner = tmp_path / 'generate.sh'
    runner.write_text(definitions + '\nwrite_remote_scripts "$1"\n')
    destination = tmp_path / 'remote'
    result = subprocess.run(['bash', str(runner), str(destination)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    start = (destination / 'start.sh').read_text()
    stop = (destination / 'stop.sh').read_text()
    assert 'python' in start
    assert 'ExecStart=$PYTHON -m wizzy.homework_import_worker' in start
    assert 'systemctl enable --now wizzy-homework-import.service' in start
    assert 'systemctl is-active --quiet wizzy-homework-import.service' in start
    import_unit = start.split('Description=Wizzy daily homework import worker', 1)[1]
    assert 'Type=notify' in import_unit
    assert 'NotifyAccess=main' in import_unit
    assert 'TimeoutStartSec=45' in import_unit
    assert 'systemctl stop wizzy-homework-import.service' in stop
    assert 'wizzy.homework_grading' in start
    for script in destination.glob('*.sh'):
        checked = subprocess.run(['bash', '-n', str(script)], capture_output=True, text=True)
        assert checked.returncode == 0, checked.stderr
