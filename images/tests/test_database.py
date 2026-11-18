import unittest

from images.database import environment


class ConnectionEnvironmentTests(unittest.TestCase):
    def test_encoded_credentials_and_serverless_tls_options_are_preserved(self):
        env = environment("postgres://app%2Fid:pass%3Aword%40host@db.example:5432/lite-typednotes-db?sslmode=require&options=databaseid%3Dfixture")
        self.assertEqual(env["PGUSER"], "app/id")
        self.assertEqual(env["PGPASSWORD"], "pass:word@host")
        self.assertEqual(env["PGHOST"], "db.example")
        self.assertEqual(env["PGDATABASE"], "lite-typednotes-db")
        self.assertEqual(env["PGSSLMODE"], "require")
        self.assertEqual(env["PGOPTIONS"], "databaseid=fixture")

    def test_socket_parameters_are_supported_and_unknown_options_are_refused(self):
        self.assertEqual(environment("postgresql://user@/app?host=%2Ftmp%2Fpostgres&port=5433")["PGHOST"], "/tmp/postgres")
        with self.assertRaises(ValueError):
            environment("postgres://user@db/app?unknown=option")
        with self.assertRaises(ValueError):
            environment("https://user@db/app")
