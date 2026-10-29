"""Voices of the assistant: the library of neural voices, the settings, the choice of a voice and the commands."""
import io
import json
import sys
import tarfile
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

with mock.patch.dict(sys.modules, {"pyttsx3": mock.MagicMock()}):
    from src.speech import voice_commands
    from src.speech import voices as V
    from src.speech.tts_module import TTS, clean_text, split_sentences


def fake_onnx(metadata=None):
    """The smallest thing that looks like an ONNX file: a protobuf with a graph (field 7) and the metadata."""
    data = b"\x3a\x04test" + b"\x3a" + bytes([5]) + b"graph"  # fields 7: tag 0x3a
    if metadata:
        data = V.add_onnx_metadata(data, metadata)
    return data


SHERPA_META = {"model_type": "vits", "n_speakers": 1, "sample_rate": 22050, "language": "Russian"}


def piper_config(speakers=1):
    return {"audio": {"sample_rate": 22050}, "espeak": {"voice": "ru"}, "num_speakers": speakers,
            "language": {"code": "ru_RU", "name_english": "Russian"}, "phoneme_id_map": {"_": [0], "^": [1], "а": [14]}}


class OnnxMetadataTests(unittest.TestCase):
    def test_add_and_read(self):
        data = V.add_onnx_metadata(fake_onnx(), {"model_type": "vits", "n_speakers": 3})
        self.assertEqual(V.read_onnx_metadata(data), {"model_type": "vits", "n_speakers": "3"})

    def test_existing_keys_are_not_overwritten(self):
        data = V.add_onnx_metadata(fake_onnx({"model_type": "vits"}), {"model_type": "other", "extra": "1"})
        self.assertEqual(V.read_onnx_metadata(data), {"model_type": "vits", "extra": "1"})

    def test_looks_like_onnx(self):
        self.assertTrue(V.looks_like_onnx(fake_onnx()))
        self.assertFalse(V.looks_like_onnx(b"hello world, this is not a model"))
        self.assertFalse(V.looks_like_onnx(b""))


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.path = self.dir / "tts.json"

    def test_defaults(self):
        s = V.TtsSettings(self.path)
        self.assertEqual((s.voice, s.rate, s.volume), (None, 1.0, 1.0))

    def test_saved_and_loaded(self):
        s = V.TtsSettings(self.path)
        s.set_voice("irina")
        s.set_rate(1.3)
        s.set_volume(0.5)
        again = V.TtsSettings(self.path)
        self.assertEqual((again.voice, again.rate, again.volume), ("irina", 1.3, 0.5))

    def test_values_are_limited(self):
        s = V.TtsSettings(self.path)
        s.set_rate(9)
        s.set_volume(-1)
        self.assertEqual((s.rate, s.volume), (1.6, 0.1))

    def test_broken_file_gives_defaults(self):
        self.path.write_text("{not json", encoding="utf-8")
        s = V.TtsSettings(self.path)
        self.assertEqual((s.voice, s.rate), (None, 1.0))
        self.path.write_text(json.dumps({"voice": 5, "rate": "fast", "volume": True, "speaker": -2}), encoding="utf-8")
        s = V.TtsSettings(self.path)
        self.assertEqual((s.rate, s.volume, s.speaker), (1.0, 1.0, 0))


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.lib = V.VoiceLibrary(root=self.dir / "voices", downloader=self.fake_download)
        self.requested = []

    def fake_download(self, url, destination, progress=None, cancel=None):
        self.requested.append(url)
        if cancel is not None and cancel.is_set():
            raise V.VoiceError("Загрузка отменена.")
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:bz2") as tar:
            def add(name, content):
                info = tarfile.TarInfo(name)
                info.size = len(content)
                tar.addfile(info, io.BytesIO(content))
            if url.endswith("espeak-ng-data.tar.bz2"):
                add("espeak-ng-data/phondata", b"x")
            else:
                folder = url.rsplit("/", 1)[1].replace(".tar.bz2", "")
                add(f"{folder}/model.onnx", fake_onnx(SHERPA_META))
                add(f"{folder}/tokens.txt", "а 14\n".encode())
                add(f"{folder}/espeak-ng-data/phondata", b"x")
        destination.write_bytes(buffer.getvalue())
        if progress:
            progress(1.0)

    def piper_files(self, name="ru_RU-test-medium", config=True, meta=None):
        folder = self.dir / "piper"
        folder.mkdir(exist_ok=True)
        model = folder / f"{name}.onnx"
        model.write_bytes(fake_onnx(meta))
        if config:
            (folder / f"{name}.onnx.json").write_text(json.dumps(piper_config()), encoding="utf-8")
        return model

    def test_nothing_installed_at_first(self):
        self.assertEqual(self.lib.installed(), [])
        self.assertFalse(self.lib.has_shared_data())

    def test_download_from_catalog(self):
        voice = self.lib.download("irina")
        self.assertEqual((voice.id, voice.name, voice.kind, voice.source), ("irina", "Ирина", V.NEURAL, V.CATALOG_SOURCE))
        self.assertTrue(self.lib.has_shared_data())
        self.assertTrue(self.lib.is_installed("irina"))
        self.assertEqual([v.id for v in self.lib.installed()], ["irina"])
        self.assertTrue(self.requested[0].endswith("vits-piper-ru_RU-irina-medium.tar.bz2"))

    def test_download_unknown_voice(self):
        with self.assertRaises(V.VoiceError):
            self.lib.download("nobody")

    def test_download_can_be_cancelled(self):
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(V.VoiceError):
            self.lib.download("denis", cancel=cancel)
        self.assertEqual(self.lib.installed(), [])
        self.assertEqual(list((self.dir / "voices").iterdir()), [])  # no leftovers

    def test_import_piper_files(self):
        voice = self.lib.import_voice(self.piper_files(), name="Мой голос")
        self.assertEqual((voice.name, voice.source, voice.removable), ("Мой голос", V.CUSTOM, True))
        meta = V.read_onnx_metadata((voice.path / "model.onnx").read_bytes())
        self.assertEqual(meta["model_type"], "vits")
        self.assertEqual(meta["sample_rate"], "22050")
        self.assertIn("а 14", (voice.path / "tokens.txt").read_text(encoding="utf-8"))
        # the pronunciation data is downloaded once, because it was missing
        self.assertTrue(self.lib.has_shared_data())
        self.assertTrue(self.requested[0].endswith("espeak-ng-data.tar.bz2"))

    def test_import_without_name_uses_file_name(self):
        voice = self.lib.import_voice(self.piper_files("ru_RU-masha-medium"))
        self.assertEqual(voice.name, "ru RU masha medium")

    def test_same_name_gets_another_id(self):
        first = self.lib.import_voice(self.piper_files(), name="Голос")
        second = self.lib.import_voice(self.piper_files(), name="Голос")
        self.assertNotEqual(first.id, second.id)
        self.assertEqual(len(self.lib.installed()), 2)

    def test_import_ready_sherpa_folder(self):
        folder = self.dir / "vits-piper-ru_RU-x-medium"
        folder.mkdir()
        (folder / "model.onnx").write_bytes(fake_onnx(SHERPA_META))
        (folder / "tokens.txt").write_text("а 14\n", encoding="utf-8")
        (folder / "espeak-ng-data").mkdir()
        (folder / "espeak-ng-data" / "phondata").write_bytes(b"x")
        voice = self.lib.import_voice(folder, name="Из папки")
        self.assertEqual(voice.name, "Из папки")
        self.assertEqual(self.requested, [])  # the data came with the folder: nothing to download

    def test_import_zip(self):
        archive = self.dir / "voice.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("v/model.onnx", fake_onnx(SHERPA_META))
            zf.writestr("v/tokens.txt", "а 14\n")
        voice = self.lib.import_voice(archive, name="Архив")
        self.assertTrue(voice.path.joinpath("model.onnx").exists())

    def test_zip_with_path_outside_is_refused(self):
        archive = self.dir / "evil.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("../evil.txt", "x")
        with self.assertRaises(V.VoiceError):
            self.lib.import_voice(archive)
        self.assertFalse((self.dir / "evil.txt").exists())

    def test_import_errors(self):
        with self.assertRaises(V.VoiceError):
            self.lib.import_voice(self.dir / "missing.onnx")
        junk = self.dir / "junk.onnx"
        junk.write_bytes(b"hello world, this is not a model")
        with self.assertRaises(V.VoiceError):
            self.lib.import_voice(junk)
        with self.assertRaises(V.VoiceError) as ctx:  # a model without its .onnx.json
            self.lib.import_voice(self.piper_files("alone", config=False))
        self.assertIn(".onnx.json", str(ctx.exception))
        with self.assertRaises(V.VoiceError):          # not a VITS model
            self.lib.import_voice(self.piper_files("other", meta={"model_type": "matcha"}))
        self.assertEqual(self.lib.installed(), [])

    def test_remove(self):
        self.lib.download("ruslan")
        self.lib.remove("ruslan")
        self.assertFalse(self.lib.is_installed("ruslan"))
        with self.assertRaises(V.VoiceError):
            self.lib.remove("ruslan")

    def test_slugify(self):
        self.assertEqual(V.slugify("Мой Голос №2"), "moy-golos-2")
        self.assertEqual(V.slugify("!!!"), "voice")


class FakeSystem:
    def __init__(self):
        self.said = []
        self._voices = [V.Voice("sapi:irina", "Irina (Russian)", V.SYSTEM), V.Voice("sapi:david", "David", V.SYSTEM)]
        self.default_id = "sapi:irina"

    def voices(self):
        return list(self._voices)

    def say(self, text, voice_id, rate, volume, speaker=0):
        self.said.append((text, voice_id, rate, volume))

    def stop(self):
        pass


class FakeNeural:
    def __init__(self):
        self.said = []
        self.fail = False

    def available(self):
        return True

    def say(self, text, voice, rate, volume, speaker=0):
        if self.fail:
            raise RuntimeError("model broken")
        self.said.append((text, voice.id))

    def stop(self):
        pass


class TtsTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.library = V.VoiceLibrary(root=self.dir / "voices")
        voice = self.dir / "voices" / "denis"
        voice.mkdir(parents=True)
        (voice / "model.onnx").write_bytes(fake_onnx(SHERPA_META))
        (voice / "tokens.txt").write_text("а 14\n", encoding="utf-8")
        (voice / "voice.json").write_text(json.dumps({"name": "Денис", "source": "catalog"}), encoding="utf-8")
        self.system, self.neural = FakeSystem(), FakeNeural()
        self.tts = TTS(library=self.library, settings=V.TtsSettings(self.dir / "tts.json"),
                       system_engine_factory=lambda: self.system, neural_engine=self.neural)
        self.states = []
        self.tts.on_status = self.states.append

    def test_default_voice_is_the_one_of_windows(self):
        self.assertEqual(self.tts.current_voice().id, "sapi:irina")
        self.tts.speak("Привет")
        self.assertEqual(self.system.said[0][:2], ("Привет", "sapi:irina"))
        self.assertEqual(self.states, ["busy", "ok"])

    def test_voice_list_has_both_kinds(self):
        self.assertEqual([v.id for v in self.tts.voices()], ["sapi:irina", "sapi:david", "denis"])

    def test_chosen_neural_voice_is_used_and_remembered(self):
        self.tts.set_voice("denis")
        self.tts.speak("Привет")
        self.assertEqual(self.neural.said, [("Привет", "denis")])
        self.assertEqual(V.TtsSettings(self.dir / "tts.json").voice, "denis")

    def test_unknown_voice_is_an_error(self):
        with self.assertRaises(V.VoiceError):
            self.tts.set_voice("nobody")

    def test_removed_voice_falls_back_to_default(self):
        self.tts.set_voice("denis")
        self.library.remove("denis")
        self.assertEqual(self.tts.current_voice().id, "sapi:irina")

    def test_broken_neural_voice_does_not_make_the_assistant_silent(self):
        self.tts.set_voice("denis")
        self.neural.fail = True
        self.tts.speak("Привет")
        self.assertEqual(self.system.said[0][0], "Привет")
        self.assertEqual(self.states[-1], "ok")

    def test_preview_uses_the_given_voice(self):
        self.tts.preview("denis", "Проба")
        self.tts.speak("после")  # said after the preview: the jobs go in order
        self.assertEqual(self.neural.said, [("Проба", "denis")])

    def test_rate_and_volume_reach_the_engine(self):
        self.tts.set_rate(1.4)
        self.tts.set_volume(0.5)
        self.tts.speak("Привет")
        self.assertEqual(self.system.said[0][2:], (1.4, 0.5))

    def test_next_voice_goes_round(self):
        names = [self.tts.next_voice().id for _ in range(4)]
        self.assertEqual(names, ["sapi:david", "denis", "sapi:irina", "sapi:david"])

    def test_find_voice_by_spoken_name(self):
        self.assertEqual(self.tts.find_voice("денис").id, "denis")
        self.assertEqual(self.tts.find_voice("david").id, "sapi:david")
        self.assertIsNone(self.tts.find_voice("шарик"))

    def test_text_is_cleaned_before_speaking(self):
        self.tts.speak("**Привет** 😀 `мир`")
        self.assertEqual(self.system.said[0][0], "Привет мир")

    def test_empty_text_is_not_spoken(self):
        self.tts.speak("***")
        self.assertEqual(self.system.said, [])

    def test_split_sentences(self):
        self.assertEqual(split_sentences("Да. Это длинное предложение. Ещё одно!"),
                         ["Да. Это длинное предложение.", "Ещё одно!"])
        self.assertEqual(clean_text("a  b"), "a b")


class VoiceCommandTests(unittest.TestCase):
    def setUp(self):
        TtsTests.setUp(self)

    def say(self, query):
        return voice_commands.handle(self.tts, query)

    def test_change_voice(self):
        self.assertIn("David", self.say("смени голос"))
        self.assertEqual(self.tts.current_voice().id, "sapi:david")

    def test_named_voice(self):
        self.assertIn("Денис", self.say("говори голосом денис"))
        self.assertEqual(self.tts.current_voice().id, "denis")

    def test_which_voice(self):
        self.assertIn("Irina", self.say("каким голосом ты говоришь"))

    def test_rate_and_volume(self):
        self.say("говори быстрее")
        self.assertAlmostEqual(self.tts.settings.rate, 1.15)
        self.say("говори медленнее")
        self.say("говори медленнее")
        self.assertAlmostEqual(self.tts.settings.rate, 0.85)
        self.say("говори тише")
        self.assertAlmostEqual(self.tts.settings.volume, 0.8)
        self.say("говори громче")
        self.say("говори громче")
        self.assertEqual(self.tts.settings.volume, 1.0)

    def test_other_phrases_are_not_commands(self):
        for phrase in ("какая погода", "включи музыку", "", "расскажи про голос птиц"):
            self.assertIsNone(self.say(phrase), phrase)


if __name__ == "__main__":
    unittest.main()
