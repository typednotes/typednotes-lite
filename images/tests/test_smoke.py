import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from images.smoke import docker


class DockerOutputTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        executable = Path(self.directory.name) / "docker"
        executable.write_text(f"#!{sys.executable}\n" + '''
import os, sys
args = sys.argv[1:]
if args[0] == "container":
    args = args[1:]
if args[0] == "logs":
    if args[1] == "missing":
        os.write(2, b"Error response from daemon: No such container\\n")
        sys.exit(1)
    os.write(1, b"service started\\n")
    os.write(2, b"local: Another Local instance is already using this directory\\n")
elif args[0] == "inspect":
    os.write(1, b'{"State":{"Running":true}}\\n')
    os.write(2, b"CLI warning must not corrupt JSON\\n")
else:
    os.write(2, b"container command failed\\n")
    sys.exit(1)
''')
        executable.chmod(0o755)
        self.environment = os.environ | {"PATH": self.directory.name + ":" + os.environ["PATH"]}

    def test_container_stderr_reaches_the_exclusive_directory_assertion(self):
        for command in (("logs",), ("container", "logs")):
            with self.subTest(command=command):
                output = docker(*command, "conflict", env=self.environment)
                self.assertEqual(output, "service started\nlocal: Another Local instance is already using this directory")
                self.assertIn("already using", output)

    def test_machine_readable_commands_keep_stderr_separate(self):
        output = docker("inspect", "fixture", env=self.environment)
        self.assertEqual(json.loads(output), {"State": {"Running": True}})

    def test_failed_commands_retain_their_diagnostics(self):
        with self.assertRaisesRegex(RuntimeError, "No such container"):
            docker("logs", "missing", env=self.environment)
        self.assertIn("No such container", docker("logs", "missing", env=self.environment, check=False))
        with self.assertRaisesRegex(RuntimeError, "container command failed"):
            docker("run", env=self.environment)
