# ENTSO-E AWS Lambda Scraper

AWS Lambda that fetches ENTSO-E Transparency Platform data and stores it as CSV in S3,
deployed with Terraform and Terragrunt.

## Architecture

**Compute & Networking:**
- Lambda function running in a private subnet with VPC configuration
- NAT instance for internet egress, using the `RaJiska/fck-nat/aws` module
- EventBridge schedule for automated scraping

**Data & Storage:**
- Private S3 bucket with KMS encryption and versioning
- CSV outputs organized by job and timestamp

**Security:**
- AWS Secrets Manager for the ENTSO-E API token
- KMS customer-managed keys (with rotation) for S3, CloudWatch Logs and Secrets Manager
- Private subnet isolation with security groups
- No public access to S3 buckets

**Observability:**
- CloudWatch Logs with 14-day retention
- CloudWatch alarms for errors, duration and throttling
- CloudWatch dashboard
- Structured JSON log line per job, with per-stage timings
- Log metric filters for successful and failed scrapes (`Custom/<name>` namespace)

## Repository Layout

```text
src/entsoe_lambda/handler.py   Lambda handler and ENTSO-E XML parser
terraform/                     Reusable Terraform module
  ├── main.tf                  Core infrastructure resources
  ├── monitoring.tf            CloudWatch dashboard and metric filters
  ├── variables.tf             Input variables
  ├── outputs.tf               Output values
  └── versions.tf              Terraform and provider versions
shared/                        Terragrunt environments (one S3 state bucket each)
  ├── root.hcl                 Shared config: S3 backend, module source, common inputs
  ├── dev/terragrunt.hcl       Dev inputs
  └── prod/terragrunt.hcl      Prod inputs
```

## Data Handling

Scrape targets are configured with `scrape_jobs` (default in `shared/root.hcl`). They use
the official ENTSO-E REST API:

```hcl
scrape_jobs = [
  {
    name              = "sk-day-ahead-generation-forecast"
    token_query_param = "securityToken"
    url               = "https://web-api.tp.entsoe.eu/api?documentType=A71&processType=A01&in_Domain=10YSK-SEPS-----K&periodStart=202601010000&periodEnd=202601020000"
  }
]
```

The Lambda parses ENTSO-E API XML (`*MarketDocument`) only:

- Each `<Point>` becomes one row, with a computed `timestamp`, `position`, the point
  values (e.g. `quantity`), `resolution`, and the `TimeSeries` metadata (bidding zone,
  unit, business type, …).
- For `curveType` A03, missing positions repeat the previous value, so each period is a
  complete, regular series.
- An `Acknowledgement_MarketDocument` (e.g. "No matching data found") fails the run with
  the reason. Any other document type fails as unsupported.

Example output:

```text
timestamp,position,quantity,resolution,mRID,businessType,objectAggregation,inBiddingZone_Domain.mRID,inBiddingZone_Domain.mRID.@codingScheme,quantity_Measure_Unit.name,curveType
2026-01-01T00:00Z,1,3141.6,PT60M,1,A01,A01,10YSK-SEPS-----K,A01,MAW,A03
2026-01-01T01:00Z,2,2940.25,PT60M,1,A01,A01,10YSK-SEPS-----K,A01,MAW,A03
```

The Lambda uses only the Python standard library plus `boto3`, which the AWS Lambda
Python runtime provides, so no dependency packaging step is needed.

### ENTSO-E API token (required)

The API returns `401` without a token. To get one (free):

1. Register at https://transparency.entsoe.eu/.
2. Email `transparency@entsoe.eu` with the subject **"Restful API access"** and your
   account email in the body.
3. Once access is granted, generate the token under **My Account Settings → Web API
   Security Token**. The token is a UUID (`xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`), not
   your account password.

Provide it to the deployment with:

```bash
export TF_VAR_entsoe_security_token="your-token"
```

Terraform stores it in Secrets Manager, and the Lambda appends it as `securityToken=...`.

> Don't point jobs at the `iop-transparency.entsoe.eu` web UI. It's a JavaScript app,
> so the HTML has no data, and it returns `HTTP 403` to AWS IP ranges (NAT egress).

## Deployment with Terragrunt

Environments are folders. `shared/root.hcl` defines the S3 backend, the module source
(`terraform/`) and common inputs. `shared/dev` and `shared/prod` contain only the values
that differ. Each environment has **its own S3 state bucket**:

| Environment | Folder | State bucket | Resource prefix |
|-------------|--------|--------------|-----------------|
| dev  | `shared/dev`  | `entsoe-scraper-tfstate-dev-<account-id>`  | `entsoe-scraper-dev` (on-demand t4g.micro NAT) |
| prod | `shared/prod` | `entsoe-scraper-tfstate-prod-<account-id>` | `entsoe-scraper-prod` |

Locking uses S3 native lockfiles (`use_lockfile`, Terraform >= 1.10), so no DynamoDB
table is needed. The default region is `eu-north-1`.

**Requirements:** Terraform >= 1.10, Terragrunt, AWS CLI.

### 1. Credentials

```bash
export AWS_PROFILE=default        # or AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY
export AWS_REGION=eu-north-1
aws sts get-caller-identity       # check you're in the intended account
export TF_VAR_entsoe_security_token="your-token"   # never put it in .hcl files
```

### 2. Bootstrap the state bucket (once per environment)

```bash
ENV=dev   # or prod. Must be set: an empty ENV produces "entsoe-scraper-tfstate--<id>"
: "${ENV:?set ENV to dev or prod}"
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
BUCKET="entsoe-scraper-tfstate-${ENV}-${ACCOUNT_ID}"
echo "Creating $BUCKET"

aws s3api create-bucket --bucket "$BUCKET" --region eu-north-1 \
  --create-bucket-configuration LocationConstraint=eu-north-1
aws s3api put-bucket-versioning --bucket "$BUCKET" \
  --versioning-configuration Status=Enabled
aws s3api put-public-access-block --bucket "$BUCKET" \
  --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
aws s3api put-bucket-encryption --bucket "$BUCKET" \
  --server-side-encryption-configuration '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'
```

**If `CreateBucket` fails with "explicit deny in a service control policy":** a service
control policy (SCP) is blocking it, and IAM permissions can't override an SCP. On AWS
Free plan accounts this policy is managed by AWS; on this account it blocks the whole
`eu-central-1` region. Use an allowed region, or use a bucket you can already write to:

```bash
export TG_STATE_BUCKET=existing-approved-bucket
```

The state key is `entsoe-scraper/<env>/terraform.tfstate`, so dev and prod can share one
bucket safely.

### 3. Deploy dev

```bash
cd shared/dev
terragrunt init
terragrunt plan -out=dev.tfplan
terragrunt apply dev.tfplan

# Smoke test
aws lambda invoke \
  --function-name "$(terragrunt output -raw lambda_function_name)" \
  /tmp/entsoe-dev-response.json && cat /tmp/entsoe-dev-response.json
aws s3 ls "s3://$(terragrunt output -raw output_bucket_name)/entsoe/" --recursive
```

Prod works the same way from `shared/prod`. Running from `shared/` with
`terragrunt run-all plan` covers every environment (`terragrunt run --all plan` on
Terragrunt >= 0.73). Use that with care.

### 4. Tear down

Tearing down also stops the NAT instance cost:

```bash
cd shared/dev
terragrunt destroy
```

Notes:
- The folder you run from selects the environment. There is no workspace to switch and
  no `-var-file` to pass.
- Terragrunt copies the module into `.terragrunt-cache/` (git-ignored). The Lambda source
  path is passed in as `lambda_source_dir`, so packaging still works from the cache.
- `terraform/` can also be applied directly as a plain module (`terraform init && terraform
  apply`). That uses local state and the defaults in `variables.tf`, so prefer Terragrunt
  for real environments.
- Spot NAT (`use_spot_nat = true`) needs an account that allows Spot. Free plan accounts
  reject it with `UnfulfillableCapacity`.

## Monitoring

Replace `<name>` with the environment's resource prefix (e.g. `entsoe-scraper-dev`):

```bash
aws cloudwatch get-dashboard --dashboard-name <name>
aws logs tail /aws/lambda/<name> --follow
aws cloudwatch describe-alarms --alarm-name-prefix <name>
```

Each job writes one JSON log line. That gives the per-stage breakdown without tracing
(X-Ray adds nothing for a single Lambda calling one external API):

```json
{"event": "scrape_complete", "job": "sk-day-ahead-generation-forecast", "http_status": 200, "bytes": 5123, "fetch_ms": 412, "rows": 24, "parse_ms": 3, "s3_key": "entsoe/...csv", "upload_ms": 85, "total_ms": 500}
{"event": "scrape_failed", "job": "sk-day-ahead-generation-forecast", "http_status": 401, "stage": "fetch", "error_type": "FetchError", "error": "HTTP 401 ...", "total_ms": 230}
```

The `SuccessfulScrapes` and `FailedScrapes` metrics count these events. Logs Insights
query for timings and failures:

```bash
aws logs start-query --log-group-name /aws/lambda/<name> \
  --start-time $(( $(date +%s) - 7*86400 )) --end-time $(date +%s) \
  --query-string 'filter ispresent(event)
    | fields @timestamp, event, job, http_status, rows, fetch_ms, parse_ms, upload_ms, total_ms, stage, error
    | sort @timestamp desc | limit 50'
# then: aws logs get-query-results --query-id <id>
```

## Adding Another Endpoint

Add another object to `scrape_jobs`. For example:

```hcl
{
  name              = "sk-actual-generation-per-unit"
  token_query_param = "securityToken"
  # documentType=A73 + processType=A16 = Actual Generation per Generation Unit (16.1.A)
  url               = "https://web-api.tp.entsoe.eu/api?documentType=A73&processType=A16&in_Domain=10YSK-SEPS-----K&periodStart=202601010000&periodEnd=202601020000"
}
```

Run `terragrunt apply` in `shared/<env>` to update the Lambda environment. The next
invocation writes one CSV per configured job.

## Assumptions

- The selected control area is Slovakia / SEPS (`10YSK-SEPS-----K`), matching the assignment example.
- The default schedule is daily. Change `schedule_expression` for other cadences.
- The query period is fixed in the job URL (`periodStart` / `periodEnd`).
- The deployment uses one public subnet and one private subnet to keep the assignment inexpensive and focused.
- `force_destroy` is enabled on the S3 bucket so `terragrunt destroy` can clean up assignment resources without manual object deletion.
