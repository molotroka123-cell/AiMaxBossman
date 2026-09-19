"""The current bundled FFmpeg and FFmpeg 6 use different file-graph options."""
import subprocess

import pytest

from bcc.video_studio.render import filter_graph_option


def test_graph_file_option_tracks_the_installed_binary(monkeypatch):
    calls = []

    def help_output(argv, **kwargs):
        calls.append(argv)
        help_text = ("-filter_complex_script filename read graph from file\n"
                     if len(calls) == 1 else "-filter_complex <graph_description> create graph\n")
        return subprocess.CompletedProcess(argv, 0, help_text, "")

    monkeypatch.setattr(subprocess, "run", help_output)
    filter_graph_option.cache_clear()
    try:
        assert filter_graph_option("ffmpeg", 10, 100) == "-filter_complex_script"
        assert filter_graph_option("ffmpeg", 10, 100) == "-filter_complex_script"
        assert len(calls) == 1, "do not run a help subprocess on every preview"
        assert filter_graph_option("ffmpeg", 11, 100) == "-/filter_complex"
        assert len(calls) == 2, "replacing the executable must invalidate capability discovery"
    finally:
        filter_graph_option.cache_clear()


def test_failed_capability_probe_is_not_cached_as_modern_support(monkeypatch):
    def fail(argv, **kwargs):
        raise subprocess.CalledProcessError(1, argv)

    monkeypatch.setattr(subprocess, "run", fail)
    filter_graph_option.cache_clear()
    with pytest.raises(subprocess.CalledProcessError):
        filter_graph_option("ffmpeg", 10, 100)
    assert filter_graph_option.cache_info().currsize == 0
