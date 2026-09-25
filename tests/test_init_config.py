import pytest

from ghrunner import init_config


@pytest.mark.parametrize(
    "raw",
    [
        "my-org/my-repo",
        "github.com/my-org/my-repo",
        "https://github.com/my-org/my-repo.git/",
    ],
)
def test_parse_repo_accepts_slugs_and_urls(raw):
    assert init_config.parse_repo(raw) == "my-org/my-repo"


@pytest.mark.parametrize(
    "parse, raw",
    [
        (init_config.parse_repo, "my-repo"),
        (init_config.parse_app_id, "abc"),
        (init_config.parse_count, "0"),
        (init_config.parse_count, "two"),
        (init_config.parse_name_prefix, "-runner"),
        (init_config.parse_labels, " , "),
        (init_config.parse_cpus, "0"),
        (init_config.parse_memory, "6 GB"),
        (init_config.parse_image_tag, ""),
    ],
)
def test_parsers_reject_bad_input(parse, raw):
    with pytest.raises(ValueError):
        parse(raw)


def test_parse_path_handles_pasted_paths(tmp_path):
    assert init_config.parse_path("'/a b/c.pem'") == init_config.parse_path(
        "/a\\ b/c.pem"
    )


def test_parse_dockerfile_dir_requires_dockerfile(tmp_path):
    with pytest.raises(ValueError):
        init_config.parse_dockerfile_dir(str(tmp_path))
    (tmp_path / "Dockerfile").write_text("FROM scratch\n")
    assert init_config.parse_dockerfile_dir(str(tmp_path)) == tmp_path.resolve()
