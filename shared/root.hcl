# Root Terragrunt config shared by every environment under shared/.
# Each environment folder (shared/dev, shared/prod) includes this file and
# supplies only its own inputs.

locals {
  aws_region = "eu-north-1"
  env        = basename(get_original_terragrunt_dir()) # "dev" | "prod"
  account_id = get_aws_account_id()

  # One state bucket per environment by default, so dev and prod state are fully isolated.
  # Override with TG_STATE_BUCKET to use an existing bucket (e.g. one pre-approved by
  # your AWS Organization). The state key includes the env, so a shared bucket is safe.
  state_bucket = coalesce(get_env("TG_STATE_BUCKET", ""), "entsoe-scraper-tfstate-${local.env}-${local.account_id}")
}

# Remote state with S3 native locking (Terraform >= 1.10). No DynamoDB table needed.
# Create each bucket once before the first run (see README "Deploy with Terragrunt").
generate "backend" {
  path      = "backend.tf"
  if_exists = "overwrite_terragrunt"
  contents  = <<-HCL
    terraform {
      backend "s3" {
        bucket       = "${local.state_bucket}"
        key          = "entsoe-scraper/${local.env}/terraform.tfstate"
        region       = "${local.aws_region}"
        encrypt      = true
        use_lockfile = true
      }
    }
  HCL
}

terraform {
  # Reusable module. Terragrunt copies it into .terragrunt-cache and runs it there.
  source = "${get_parent_terragrunt_dir()}/../terraform"
}

inputs = {
  aws_region = local.aws_region

  # The module runs from the cache copy, so point it at the real source dir.
  lambda_source_dir = "${get_parent_terragrunt_dir()}/../src"

  output_prefix   = "entsoe"
  enable_schedule = true

  # Official ENTSO-E REST API. The iop-transparency.entsoe.eu web UI is a JavaScript app
  # (no data in the HTML) and returns HTTP 403 to AWS IP ranges, so it can't be scraped
  # from Lambda.
  # documentType=A71 + processType=A01 = Generation Forecast, Day-ahead (14.1.C),
  # Slovakia / SEPS.
  # Requires TF_VAR_entsoe_security_token (appended as ?securityToken=...).
  scrape_jobs = [
    {
      name              = "sk-day-ahead-generation-forecast"
      token_query_param = "securityToken"
      url               = "https://web-api.tp.entsoe.eu/api?documentType=A71&processType=A01&in_Domain=10YSK-SEPS-----K&periodStart=202601010000&periodEnd=202601020000&curveType=A01"
    },
    {
      name              = "sk-actual-generation-per-generation-unit"
      token_query_param = "securityToken"
      url               = "https://web-api.tp.entsoe.eu/api?documentType=A73&processType=A16&in_Domain=10YSK-SEPS-----K&periodStart=202601010000&periodEnd=202601020000&curveType=A01"
    }
  ]

  # entsoe_security_token is not set here on purpose.
  # Export TF_VAR_entsoe_security_token instead.
}
