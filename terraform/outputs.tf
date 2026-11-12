output "lambda_function_name" {
  description = "Name of the scraper Lambda function."
  value       = aws_lambda_function.scraper.function_name
}

output "output_bucket_name" {
  description = "S3 bucket receiving CSV outputs."
  value       = aws_s3_bucket.output.bucket
}

output "private_subnet_id" {
  description = "Private subnet used by Lambda."
  value       = aws_subnet.private.id
}
