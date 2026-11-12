include "root" {
  path = find_in_parent_folders("root.hcl")
}

inputs = {
  project_name = "entsoe-scraper-dev"

  vpc_cidr            = "10.40.0.0/16"
  public_subnet_cidr  = "10.40.0.0/24"
  private_subnet_cidr = "10.40.10.0/24"

  lambda_timeout_seconds = 120
  lambda_memory_mb       = 256

  # On-demand t4g.micro is free-tier eligible. Spot fails on this account with
  # "UnfulfillableCapacity ... request configuration" (Spot not available on the Free plan).
  fck_nat_instance_type = "t4g.micro"
  use_spot_nat          = false

  schedule_expression = "rate(1 day)"
  output_bucket_name  = null # auto-generated unique name
}
