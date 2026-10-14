"""Run inside the application test container, without external network access."""

import asyncio
import hashlib
import json
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace

import oracledb
import tiktoken

from dbgpt.util.code.docker_execution import run
from dbgpt_ext.datasource.rdbms.conn_oracle import initialize_oracle_client


async def main():
    assert hashlib.new("sm3", b"abc").hexdigest().startswith("66c7f0f4")
    initialize_oracle_client()
    assert not oracledb.is_thin_mode()
    print("Oracle Instant Client", oracledb.clientversion())
    for name in ("cl100k_base", "o200k_base"):
        assert tiktoken.get_encoding(name).encode("offline check")
    print("Offline tokenizer cache: PASS")
    Path("/app/pilot/tmp").mkdir(parents=True, exist_ok=True)
    cwd = Path(tempfile.mkdtemp(prefix="offline-smoke-", dir="/app/pilot/tmp"))
    script = cwd / "test.py"
    script.write_text(
        """import os, socket
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
assert "SITE_APP_SECRET" not in os.environ
assert not os.path.exists("/var/run/docker.sock")
assert not os.path.exists("/app/pilot/meta_data/dbgpt.db")
try:
    socket.create_connection(("1.1.1.1", 443), timeout=1)
except OSError:
    pass
else:
    raise AssertionError("network is enabled")
df = pd.DataFrame({"amount": [1, 2, 3]})
df.to_excel("result.xlsx", index=False)
df.plot(); plt.savefig("result.png")
print("SANDBOX_OK", int(df.amount.sum()))
""",
        encoding="utf-8",
    )
    status, out, err = await run(["python", str(script)], str(cwd), dict(os.environ))
    assert status == 0, (status, out, err)
    assert b"SANDBOX_OK 6" in out
    assert (cwd / "result.xlsx").is_file() and (cwd / "result.png").is_file()
    print("Python sandbox, isolation, Excel and chart: PASS")
    status, stdout, stderr = await run(
        ["bash", "-c", 'python -c "import pandas; print(pandas.__version__)"'],
        str(cwd),
    )
    assert status == 0 and stdout.strip(), (status, stdout, stderr)
    print("Shell preserves UV Python environment: PASS")
    status, _, _ = await run(
        ["python", "-c", "import time; time.sleep(20)"], str(cwd), timeout=1
    )
    assert status is None
    print("Sandbox timeout cleanup: PASS")

    from dbgpt.agent.skill.manage import SkillManager

    skill = cwd / "test-skill"
    (skill / "scripts").mkdir(parents=True, exist_ok=True)
    (skill / "scripts" / "analyze.py").write_text(
        '''import matplotlib.pyplot as plt
args = json.loads(sys.argv[1])
import pandas as pd
assert pd.read_excel(args["input"]).amount.sum() == 6
plt.plot([1, 2, 3]); plt.savefig("skill-result.png")
print(json.dumps({"chunks": [{"output_type": "text", "content": "SKILL_OK"}]}))
''',
        encoding="utf-8",
    )
    manager = SimpleNamespace(
        _get_skill_path=lambda name: str(skill),
        _should_reject_personal_skill_execution=lambda path: False,
        _adapt_args_for_script=lambda code, args: args,
    )
    result = json.loads(
        await SkillManager.execute_skill_script_file(
            manager,
            "test-skill",
            "analyze.py",
            {"input": str(cwd / "result.xlsx")},
            output_dir=str(cwd / "skill-output"),
        )
    )
    assert {"output_type": "text", "content": "SKILL_OK"} in result["chunks"], result
    assert any(c["output_type"] == "image" for c in result["chunks"]), result
    print("Skill sandbox, mounted input, structured output and image: PASS")


asyncio.run(main())
