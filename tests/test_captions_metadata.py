from clipper import captions, metadata
from clipper.config import match_streamer


def test_chunk_words_breaks_on_count_and_pause():
    words = [{"start": i * 0.3, "end": i * 0.3 + 0.25, "word": w} for i, w in enumerate("a b c d".split())]
    words.append({"start": 5.0, "end": 5.2, "word": "e"})
    chunks = captions.chunk_words(words, per_line=3)
    assert [[w["word"] for w in c] for c in chunks] == [["a", "b", "c"], ["d"], ["e"]]


def test_build_ass_relative_times_and_highlight():
    words = [{"start": 101.0, "end": 101.4, "word": "no"}, {"start": 101.5, "end": 101.9, "word": "way"}]
    ass = captions.build_ass(words, 100.0, 130.0, {"uppercase": True}, credit="twitch.tv/someone")
    assert "PlayResX: 1080" in ass and "PlayResY: 1920" in ass
    assert "Dialogue: 0,0:00:01.00,0:00:01.50,Caption" in ass
    assert "NO" in ass and "WAY" in ass and "\\c&H0000E5FF" in ass
    assert "Credit,,0,0,0,,twitch.tv/someone" in ass


def test_ass_escapes_override_braces():
    ass = captions.build_ass([{"start": 0.1, "end": 0.5, "word": "{\\pos(1,1)}hi"}], 0, 5, {})
    assert "\\pos" not in ass


def test_timestamped_urls():
    assert metadata.timestamped_url("https://www.twitch.tv/videos/1", 3725) == "https://www.twitch.tv/videos/1?t=1h2m5s"
    assert metadata.timestamped_url("https://www.youtube.com/watch?v=abc", 61) == "https://www.youtube.com/watch?v=abc&t=61s"
    assert metadata.timestamped_url(None, 5) == ""


def test_metadata_credit_and_limits():
    info = {"uploader": "someone", "webpage_url": "https://www.twitch.tv/videos/9", "channel_url": "https://www.twitch.tv/someone"}
    cfg = {"title_template": "{streamer} {hook} " + "x" * 120, "description_template": "{hook}\n🎥 Full stream: {source_url_t}\n👤 {streamer} — {channel_url}"}
    m = metadata.build({"hook": "they called the cops", "start": 10}, info, {"name": "Someone"}, cfg)
    assert len(m["title"]) <= 100
    assert "https://www.twitch.tv/videos/9?t=0h0m10s" in m["description"]
    assert "https://www.twitch.tv/someone" in m["description"]
    local = metadata.build({"hook": "", "start": 0}, {}, {"name": "X"}, cfg)
    assert "Full stream:" not in local["description"]


def test_match_streamer():
    allow = {"streamers": [{"name": "Some Body", "match": ["somebody", "youtube.com/@somebodylive"]}]}
    assert match_streamer({"uploader": "somebody"}, allow)
    assert match_streamer({"channel_url": "https://www.youtube.com/@SomeBodyLive"}, allow)
    assert match_streamer({"webpage_url": "https://www.twitch.tv/somebody/videos?filter=archives"}, allow)
    assert match_streamer({"uploader": "somebodyelse"}, allow) is None


def test_cli_upload_uses_job_privacy(tmp_path, monkeypatch):
    import json as _json

    from clipper import cli, youtube

    (tmp_path / "clips").mkdir()
    clip = {"name": "clip_01", "file": "clips/clip_01.mp4", "thumbnail": None, "title": "Old", "description": "d", "tags": []}
    (tmp_path / "clips" / "clip_01.json").write_text(_json.dumps({**clip, "title": "Edited"}))
    manifest = {"clips": [clip], "settings": {"youtube": {"privacy": "public"}}}
    seen = {}
    monkeypatch.setattr(youtube, "upload", lambda video, title, desc, tags, cfg, thumb=None: seen.update(title=title, privacy=cfg["privacy"]) or {"url": "u"})
    cli._upload_dir(tmp_path, manifest)
    assert seen == {"title": "Edited", "privacy": "public"}


def test_ui_save_keeps_presets(monkeypatch, tmp_path):
    from clipper import config

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    config.save_streamers({"streamers": [{"name": "A", "match": ["a"], "preset": {"cta": "SUB"}}]})
    config.save_streamers({"streamers": [{"name": "A", "match": ["a", "aa"], "credit_url": "u"}]})  # UI payload
    s = config.load_streamers()["streamers"][0]
    assert s["match"] == ["a", "aa"] and s["preset"] == {"cta": "SUB"}


def test_hashtags_rotate_but_keep_required():
    cfg = {"title_template": "{hook} {hashtags}", "required_hashtags": ["#tomovids", "#gta6"],
           "hashtag_pool": ["#a", "#b", "#c", "#d", "#e", "#f"], "hashtag_extra": 3}
    titles = {metadata.build({"hook": "x", "start": s}, {"id": "v"}, {"name": "T"}, cfg)["title"] for s in range(0, 600, 60)}
    assert all(t.split()[1:3] == ["#tomovids", "#gta6"] and len(t.split()) == 6 for t in titles)
    assert len(titles) > 1   # extra hashtags differ between clips
