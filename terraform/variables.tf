variable "project_name" {
  description = "Prefix used for named AWS resources."
  type        = string
  default     = "entsoe-scraper"
}

variable "aws_region" {
  description = "AWS region to deploy into."
  type        = string
  default     = "eu-north-1"
}

variable "vpc_cidr" {
  description = "CIDR block for the scraper VPC."
  type        = string
  default     = "10.40.0.0/16"
}

variable "public_subnet_cidr" {
  description = "CIDR block for the public subnet containing fck-nat."
  type        = string
  default     = "10.40.0.0/24"
}

variable "private_subnet_cidr" {
  description = "CIDR block for the private Lambda subnet."
  type        = string
  default     = "10.40.10.0/24"
}

variable "lambda_source_dir" {
  description = "Directory containing the Lambda package source. Defaults to ../src relative to this module. Terragrunt sets it because it runs the module from a cache copy."
  type        = string
  default     = null
}

variable "output_bucket_name" {
  description = "Optional fixed S3 bucket name. Leave null to generate a unique name."
  type        = string
  default     = null
}

variable "output_prefix" {
  description = "S3 key prefix for generated CSV files."
  type        = string
  default     = "entsoe"
}

variable "schedule_expression" {
  description = "EventBridge schedule expression for Lambda runs."
  type        = string
  default     = "rate(1 day)"
}

variable "enable_schedule" {
  description = "Whether to create the EventBridge schedule."
  type        = bool
  default     = true
}

variable "scrape_jobs" {
  description = "Scrape job definitions. Add endpoints here without changing Lambda code."
  type = list(object({
    name              = string
    url               = string
    token_query_param = optional(string)
  }))
  default = [
    {
      name              = "sk-day-ahead-generation-forecast"
      token_query_param = "securityToken"
      url               = "https://web-api.tp.entsoe.eu/api?documentType=A71&processType=A01&in_Domain=10YSK-SEPS-----K&periodStart=202601010000&periodEnd=202601020000"
    }
  ]
}

variable "entsoe_security_token" {
  description = "Optional ENTSO-E security token. Prefer TF_VAR_entsoe_security_token or a tfvars file excluded from git."
  type        = string
  default     = ""
  sensitive   = true
}

variable "lambda_timeout_seconds" {
  description = "Lambda timeout."
  type        = number
  default     = 120
}

variable "lambda_memory_mb" {
  description = "Lambda memory size."
  type        = number
  default     = 256
}

variable "fck_nat_instance_type" {
  description = "Instance type for fck-nat."
  type        = string
  default     = "t4g.micro"
}

variable "use_spot_nat" {
  description = "Whether fck-nat should use Spot instances."
  type        = bool
  default     = false
}
