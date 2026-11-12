# CloudWatch Dashboard for monitoring
resource "aws_cloudwatch_dashboard" "main" {
  dashboard_name = local.name

  dashboard_body = jsonencode({
    widgets = [
      {
        type = "metric"
        properties = {
          metrics = [
            ["AWS/Lambda", "Invocations", { stat = "Sum", label = "Invocations" }],
            [".", "Errors", { stat = "Sum", label = "Errors" }],
            [".", "Throttles", { stat = "Sum", label = "Throttles" }],
            [".", "Duration", { stat = "Average", label = "Avg Duration" }],
            ["...", { stat = "Maximum", label = "Max Duration" }]
          ]
          view    = "timeSeries"
          stacked = false
          region  = var.aws_region
          title   = "Lambda Metrics"
          period  = 300
          yAxis = {
            left = {
              label = "Count / Milliseconds"
            }
          }
        }
      },
      {
        type = "metric"
        properties = {
          metrics = [
            ["AWS/Lambda", "ConcurrentExecutions", { stat = "Maximum" }]
          ]
          view   = "timeSeries"
          region = var.aws_region
          title  = "Lambda Concurrent Executions"
          period = 300
        }
      },
      {
        type = "log"
        properties = {
          query   = "SOURCE '${aws_cloudwatch_log_group.lambda.name}'\n| fields @timestamp, @message\n| filter @message like /ERROR/\n| sort @timestamp desc\n| limit 20"
          region  = var.aws_region
          stacked = false
          title   = "Recent Lambda Errors"
          view    = "table"
        }
      },
      {
        type = "metric"
        properties = {
          metrics = [
            ["AWS/S3", "BucketSizeBytes", { stat = "Average" }],
            [".", "NumberOfObjects", { stat = "Average" }]
          ]
          view   = "singleValue"
          region = var.aws_region
          title  = "S3 Bucket Stats"
          period = 86400
        }
      }
    ]
  })
}

# Log metric filter for tracking successful scrapes
resource "aws_cloudwatch_log_metric_filter" "successful_scrapes" {
  name           = "${local.name}-successful-scrapes"
  log_group_name = aws_cloudwatch_log_group.lambda.name
  pattern        = "{ $.event = \"scrape_complete\" }"

  metric_transformation {
    name      = "SuccessfulScrapes"
    namespace = "Custom/${local.name}"
    value     = "1"
  }
}

# Log metric filter for failed scrapes
resource "aws_cloudwatch_log_metric_filter" "failed_scrapes" {
  name           = "${local.name}-failed-scrapes"
  log_group_name = aws_cloudwatch_log_group.lambda.name
  pattern        = "{ $.event = \"scrape_failed\" }"

  metric_transformation {
    name      = "FailedScrapes"
    namespace = "Custom/${local.name}"
    value     = "1"
  }
}
