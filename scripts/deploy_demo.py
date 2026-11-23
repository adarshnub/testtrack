"""Package and deploy the stateless BankLedger demo to an explicit AWS CLI profile."""

import argparse
import base64
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "apps" / "extractor"
BUILD = ROOT / "var"
NAME = "bankledger-pro-demo"


def package(output: Path, dependencies: Path, legacy_ocr: Path | None):
    if not (dependencies / "pymupdf").is_dir():
        raise SystemExit("Install the Lambda dependencies first. See README.md.")
    application = [SOURCE / name for name in ("demo.py", "normalization.py", "schemas.py", "reconciliation.py")]
    for name in ("parsers", "exporters", "demo_web"):
        application.extend(path for path in (SOURCE / name).rglob("*") if path.is_file() and "__pycache__" not in path.parts)
    dependency_dirs = [dependencies]
    if legacy_ocr is not None:
        if not (legacy_ocr / "tesserocr").is_dir() or not (legacy_ocr / "tessdata" / "eng.traineddata").is_file():
            raise SystemExit("The optional OCR directory must contain tesserocr and tessdata/eng.traineddata.")
        dependency_dirs.append(legacy_ocr)
    with ZipFile(output, "w", ZIP_DEFLATED, compresslevel=6) as archive:
        for file in application:
            archive.write(file, file.relative_to(SOURCE))
        for directory in dependency_dirs:
            for file in directory.rglob("*"):
                if (file.is_file() and "__pycache__" not in file.parts and file.name != ".lock" and file.suffix != ".pyc"
                        and file.parent.name != "bin"):
                    archive.write(file, file.relative_to(directory))


def deploy(profile: str, region: str, dependencies: Path, stage_in_s3: bool, legacy_ocr: Path | None):
    aws_exe = shutil.which("aws")
    if not aws_exe:
        raise SystemExit("Install and configure the AWS CLI first.")

    def aws(*args, allowed=None):
        result = subprocess.run([aws_exe, "--profile", profile, "--region", region, "--output", "json",
                                 "--no-cli-pager", *args], capture_output=True, text=True)
        if result.returncode:
            if allowed and allowed in result.stderr:
                return None
            raise RuntimeError(f"AWS {args[0]} {args[1]} failed: {result.stderr.strip()}")
        return json.loads(result.stdout) if result.stdout.strip() else {}

    identity = aws("sts", "get-caller-identity")
    account, partition = identity["Account"], identity["Arn"].split(":")[1]
    BUILD.mkdir(exist_ok=True)
    artifact = BUILD / "bankledger-demo-lambda.zip"
    package(artifact, dependencies, legacy_ocr)
    if not stage_in_s3 and artifact.stat().st_size > 50 * 1024 * 1024:
        raise SystemExit("The ZIP exceeds the direct Lambda upload limit. Re-run with --stage-in-s3.")
    role_name = f"{NAME}-lambda"
    log_group = f"/aws/lambda/{NAME}"
    with tempfile.TemporaryDirectory(dir=BUILD) as temp:
        def json_file(name, obj):
            path = Path(temp) / name
            path.write_text(json.dumps(obj), encoding="utf-8")
            return "file://" + str(path)

        @contextmanager
        def code_source():
            if not stage_in_s3:
                yield ("--zip-file", "fileb://" + str(artifact)), {"ZipFile": "fileb://" + str(artifact)}
                return
            bucket = f"bankledger-pro-demo-artifacts-{account}-{region}"
            create = ("--create-bucket-configuration", f"LocationConstraint={region}") if region != "us-east-1" else ()
            aws("s3api", "create-bucket", "--bucket", bucket, *create, allowed="BucketAlreadyOwnedByYou")
            aws("s3api", "put-public-access-block", "--bucket", bucket, "--public-access-block-configuration",
                "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true")
            key = f"packages/{hashlib.sha256(artifact.read_bytes()).hexdigest()}.zip"
            aws("s3", "cp", str(artifact), f"s3://{bucket}/{key}", "--sse", "AES256", "--only-show-errors")
            try:
                yield ("--s3-bucket", bucket, "--s3-key", key), {"S3Bucket": bucket, "S3Key": key}
            finally:
                aws("s3api", "delete-object", "--bucket", bucket, "--key", key)

        role = aws("iam", "get-role", "--role-name", role_name, allowed="NoSuchEntity")
        if role is None:
            trust = {"Version": "2012-10-17", "Statement": [{"Effect": "Allow",
                     "Principal": {"Service": "lambda.amazonaws.com"}, "Action": "sts:AssumeRole"}]}
            role = aws("iam", "create-role", "--role-name", role_name,
                       "--assume-role-policy-document", json_file("trust.json", trust))
        aws("logs", "create-log-group", "--log-group-name", log_group, allowed="ResourceAlreadyExistsException")
        aws("logs", "put-retention-policy", "--log-group-name", log_group, "--retention-in-days", "7")
        policy = {"Version": "2012-10-17", "Statement": [{"Effect": "Allow",
                  "Action": ["logs:CreateLogStream", "logs:PutLogEvents"],
                  "Resource": f"arn:{partition}:logs:{region}:{account}:log-group:{log_group}:*"}]}
        aws("iam", "put-role-policy", "--role-name", role_name, "--policy-name", "WriteDemoLogs",
            "--policy-document", json_file("logs.json", policy))
        existing = aws("lambda", "get-function", "--function-name", NAME, allowed="ResourceNotFoundException")
        settings = (
            "--runtime", "python3.12", "--handler", "demo.lambda_handler",
            "--role", role["Role"]["Arn"], "--timeout", "60" if legacy_ocr else "35", "--memory-size", "1024",
        )
        if existing is None:
            with code_source() as (_, source):
                # The create API puts the S3 source in a nested Code object, unlike update.
                package_args = ("--code", json_file("code.json", source)) if stage_in_s3 else ("--zip-file", source["ZipFile"])
                for attempt in range(7):
                    try:
                        aws("lambda", "create-function", "--function-name", NAME, "--architectures", "x86_64",
                            *package_args, *settings)
                        break
                    except RuntimeError as error:
                        if attempt == 6 or "cannot be assumed" not in str(error):
                            raise
                        time.sleep(5)
                aws("lambda", "wait", "function-active-v2", "--function-name", NAME)
        else:
            encoded_hash = base64.b64encode(hashlib.sha256(artifact.read_bytes()).digest()).decode("ascii")
            configuration = existing["Configuration"]
            if configuration["CodeSha256"] != encoded_hash:
                with code_source() as (package_args, _):
                    aws("lambda", "update-function-code", "--function-name", NAME, *package_args)
                    aws("lambda", "wait", "function-updated-v2", "--function-name", NAME)
            variables = configuration.get("Environment", {}).get("Variables", {})
            if "BANKLEDGER_DEMO_CODE" in variables:
                del variables["BANKLEDGER_DEMO_CODE"]
                settings += ("--environment", json_file("environment.json", {"Variables": variables}))
            aws("lambda", "update-function-configuration", "--function-name", NAME, *settings)
            aws("lambda", "wait", "function-updated-v2", "--function-name", NAME)
        if aws("lambda", "put-function-concurrency", "--function-name", NAME, "--reserved-concurrent-executions", "2",
               allowed="decreases account's UnreservedConcurrentExecution below its minimum") is None:
            print("This AWS account's minimum unreserved concurrency prevents a dedicated reservation. Using shared concurrency.")
        url = aws("lambda", "get-function-url-config", "--function-name", NAME, allowed="ResourceNotFoundException")
        if url is None:
            url = aws("lambda", "create-function-url-config", "--function-name", NAME, "--auth-type", "NONE")
        for sid, action, condition in (
            ("BrowserFunctionUrl", "lambda:InvokeFunctionUrl", ["--function-url-auth-type", "NONE"]),
            ("BrowserInvokedViaUrl", "lambda:InvokeFunction", ["--invoked-via-function-url"]),
        ):
            aws("lambda", "add-permission", "--function-name", NAME, "--statement-id", sid, "--action", action,
                "--principal", "*", *condition, allowed="ResourceConflictException")
    print(f"Demo URL: {url['FunctionUrl']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--dependencies", type=Path, default=BUILD / "demo-lambda-deps")
    parser.add_argument("--stage-in-s3", action="store_true", help="Upload the package through a private bucket in the deployment region.")
    parser.add_argument("--legacy-ocr-dependencies", type=Path, help="Include the optional Linux OCR dependency directory.")
    options = parser.parse_args()
    deploy(options.profile, options.region, options.dependencies, options.stage_in_s3, options.legacy_ocr_dependencies)
