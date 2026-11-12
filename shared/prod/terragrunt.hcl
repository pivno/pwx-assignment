include "root" {
  path = find_in_parent_folders("root.hcl")
}

inputs = {
  project_name = "entsoe-scraper-prod"

  vpc_cidr            = "10.50.0.0/16"
  public_subnet_cidr  = "10.50.0.0/24"
  private_subnet_cidr = "10.50.10.0/24"

  lambda_timeout_seconds = 180
  lambda_memory_mb       = 512

  # On-demand NAT for production reliability.
  fck_nat_instance_type = "t4g.small"
  use_spot_nat          = false

  schedule_expression = "cron(0 1 * * ? *)" # daily at 01:00 UTC
  output_bucket_name  = "entsoe-scraper-prod-output"
}
