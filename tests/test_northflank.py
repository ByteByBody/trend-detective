"""Northflank deploy: the service definition and the thin API client."""

from deploy import northflank


def test_service_payload_builds_this_repo_with_its_dockerfile():
    payload = northflank.service_payload(
        "trend-detective", "https://github.com/ByteByBody/trend-detective", "main"
    )

    assert payload["buildSource"] == "git"
    assert payload["vcsData"]["projectType"] == "github"
    assert payload["vcsData"]["projectUrl"].endswith("ByteByBody/trend-detective")
    assert payload["vcsData"]["projectBranch"] == "main"
    assert payload["buildSettings"]["dockerfile"]["dockerFilePath"] == "/Dockerfile"


def test_service_payload_uses_the_free_compute_plan_and_one_instance():
    payload = northflank.service_payload(
        "trend-detective", "https://github.com/ByteByBody/trend-detective", "main"
    )

    assert payload["billing"]["deploymentPlan"] == "nf-compute-50"
    assert payload["deployment"]["instances"] == 1


def test_service_payload_exposes_the_app_port_publicly_as_http():
    payload = northflank.service_payload(
        "trend-detective", "https://github.com/ByteByBody/trend-detective", "main"
    )

    port = payload["ports"][0]
    assert port["internalPort"] == 7860
    assert port["public"] is True
    assert port["protocol"] == "HTTP"


def test_service_payload_probes_the_health_endpoint():
    payload = northflank.service_payload(
        "trend-detective", "https://github.com/ByteByBody/trend-detective", "main"
    )

    check = payload["healthChecks"][0]
    assert check["protocol"] == "HTTP"
    assert check["path"] == "/health"
    assert check["port"] == 7860


def test_api_url_joins_paths_under_the_versioned_root():
    assert northflank.api_url("/projects") == "https://api.northflank.com/v1/projects"


def test_public_url_reads_the_code_run_domain_from_the_service():
    service = {
        "name": "trend-detective",
        "ports": [
            {
                "name": "http",
                "public": True,
                "domains": ["https://trend-detective-code.run"],
            }
        ],
    }

    assert northflank.public_url(service) == "https://trend-detective-code.run"


def test_public_url_falls_back_to_the_service_name_when_no_domain_is_reported():
    service = {"name": "trend-detective", "ports": [{"name": "http", "public": True}]}

    assert northflank.public_url(service) == "https://trend-detective.northflank.app"
