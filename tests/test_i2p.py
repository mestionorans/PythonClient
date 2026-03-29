from urllib.parse import quote

from vertex.i2p import I2P


def test_add_torrent_builds_magnet_and_calls_rpc(monkeypatch):
		# Arrange
		file_path = "example file.txt"
		torrent_id = "343b3e5adda3e7808edc6198961c630104ac3c28"
		tracker = "http://tracker2.postman.i2p/announce.php"

		calls: list[tuple[str, dict]] = []

		def fake_rpc(self, method: str, arguments: dict) -> dict:
				calls.append((method, arguments))
				return {}

		monkeypatch.setattr(I2P, "_rpc", fake_rpc)

		# Act
		client = I2P()
		client.add_torrent(file_path, torrent_id, [tracker])

		# Assert
		assert len(calls) == 1
		method, args = calls[0]
		assert method == "torrent-add"
		magnet_uri = args["filename"]

		expected = []
		expected.append(f"magnet:?xt=urn:btih:{torrent_id}")
		expected.append(f"dn={quote(file_path)}")
		expected.append(f"tr={quote(tracker)}")
		expected_uri = "&".join(expected)

		assert magnet_uri == expected_uri


def test_delete_torrent_pops_signature_and_calls_rpc(monkeypatch):
		calls: list[tuple[str, dict]] = []

		def fake_rpc(self, method: str, arguments: dict) -> dict:
				calls.append((method, arguments))
				if method == "torrent-get":
						return {"torrents": [{"id": 9, "hashString": "a" * 40}]}
				return {}

		monkeypatch.setattr(I2P, "_rpc", fake_rpc)

		client = I2P()
		client._seed_signatures = {("/tmp/fake.bin", "a" * 40): b"sig"}

		# Act: should not raise, remove local signature, and call RPC remove
		client.delete_torrent("/tmp/fake.bin", "a" * 40)

		# Assert
		assert client._seed_signatures.get(("/tmp/fake.bin", "a" * 40)) is None
		assert any(c == ("torrent-remove", {"ids": [9], "delete-local-data": False}) for c in calls)


def test_delete_torrent_numeric_id(monkeypatch):
		calls: list[tuple[str, dict]] = []

		def fake_rpc(self, method: str, arguments: dict) -> dict:
				calls.append((method, arguments))
				return {}

		monkeypatch.setattr(I2P, "_rpc", fake_rpc)

		client = I2P()
		client.delete_torrent("/tmp/fake.bin", "42")

		assert calls == [("torrent-remove", {"ids": [42], "delete-local-data": False})]


def test_count_seeders_returns_int_zero(monkeypatch):
		def fake_rpc(self, method: str, arguments: dict) -> dict:
				return {"torrents": []}

		monkeypatch.setattr(I2P, "_rpc", fake_rpc)

		client = I2P()
		n = client.count_seeders("1")
		assert isinstance(n, int)
		assert n == 0


def test_count_seeders_returns_seeder_count(monkeypatch):
		def fake_rpc(self, method: str, arguments: dict) -> dict:
				if method == "torrent-get" and arguments.get("fields") == ["seeders"]:
						return {"torrents": [{"seeders": 5}]}
				return {}

		monkeypatch.setattr(I2P, "_rpc", fake_rpc)

		client = I2P()
		n = client.count_seeders("1")
		assert n == 5


def test_count_seeders_unknown_id_no_rpc(monkeypatch):
		calls: list[tuple[str, dict]] = []

		def fake_rpc(self, method: str, arguments: dict) -> dict:
				calls.append((method, arguments))
				return {}

		monkeypatch.setattr(I2P, "_rpc", fake_rpc)

		client = I2P()
		n = client.count_seeders("not-a-valid-id")
		assert n == 0
		assert calls == []
