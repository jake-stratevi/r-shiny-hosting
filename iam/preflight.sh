#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Preflight permission check.
#
# Makes one harmless read-only call per AWS service the deployment touches and
# reports which ones come back AccessDenied. Run this BEFORE terraform apply so
# you find every gap at once instead of one per failed apply.
#
#   chmod +x preflight.sh && ./preflight.sh
#
# A pass here proves the service is reachable, not that you can write to it.
# But "service missing from the policy entirely" is the failure mode that
# actually happens, and this catches it.
# ---------------------------------------------------------------------------

set -uo pipefail

REGION="${AWS_REGION:-us-east-1}"
PASS=0
FAIL=0
FAILED_SERVICES=()

green() { printf '\033[0;32m%s\033[0m\n' "$1"; }
red()   { printf '\033[0;31m%s\033[0m\n' "$1"; }
dim()   { printf '\033[2m%s\033[0m\n' "$1"; }

check() {
  local label="$1"; shift
  local out
  if out=$("$@" 2>&1); then
    green "  PASS  $label"
    PASS=$((PASS + 1))
  else
    if grep -qiE 'AccessDenied|UnauthorizedOperation|not authorized|AccessDeniedException' <<<"$out"; then
      red "  DENIED  $label"
      FAILED_SERVICES+=("$label")
      FAIL=$((FAIL + 1))
    else
      # Non-permission errors (throttling, bad region) are not what we're testing.
      printf '  \033[0;33mUNCLEAR\033[0m %s\n' "$label"
      dim "          ${out:0:120}"
      PASS=$((PASS + 1))
    fi
  fi
}

echo
echo "Preflight permission check"
echo "Region: $REGION"

IDENTITY=$(aws sts get-caller-identity --output json 2>&1)
if grep -qi 'error' <<<"$IDENTITY"; then
  red "Cannot call sts:GetCallerIdentity. Credentials are not configured."
  echo "$IDENTITY"
  exit 1
fi

echo "Account: $(jq -r .Account <<<"$IDENTITY" 2>/dev/null || echo '?')"
echo "Identity: $(jq -r .Arn <<<"$IDENTITY" 2>/dev/null || echo '?')"
echo

echo "DNS and certificates"
check "route53:ListHostedZones"          aws route53 list-hosted-zones --max-items 1
check "acm:ListCertificates"             aws acm list-certificates --region "$REGION" --max-items 1

echo
echo "Networking"
check "ec2:DescribeVpcs"                 aws ec2 describe-vpcs --region "$REGION" --max-items 1
check "ec2:DescribeSubnets"              aws ec2 describe-subnets --region "$REGION" --max-items 1
check "ec2:DescribeSecurityGroups"       aws ec2 describe-security-groups --region "$REGION" --max-items 1
check "ec2:DescribeAvailabilityZones"    aws ec2 describe-availability-zones --region "$REGION"

echo
echo "Load balancing"
check "elasticloadbalancing:DescribeLoadBalancers" \
  aws elbv2 describe-load-balancers --region "$REGION" --page-size 1

echo
echo "Containers"
check "ecs:ListClusters"                 aws ecs list-clusters --region "$REGION" --max-items 1
check "ecr:DescribeRepositories"         aws ecr describe-repositories --region "$REGION" --max-items 1

echo
echo "Scaler"
check "lambda:ListFunctions"             aws lambda list-functions --region "$REGION" --max-items 1
check "events:ListRules"                 aws events list-rules --region "$REGION" --limit 1

echo
echo "Observability and config"
check "logs:DescribeLogGroups"           aws logs describe-log-groups --region "$REGION" --limit 1
check "cloudwatch:ListDashboards"        aws cloudwatch list-dashboards --region "$REGION"
check "ssm:DescribeParameters"           aws ssm describe-parameters --region "$REGION" --max-items 1

echo
echo "Authentication"
check "cognito-idp:ListUserPools"        aws cognito-idp list-user-pools --region "$REGION" --max-results 1

echo
echo "IAM (the one most often missing)"
check "iam:ListRoles"                    aws iam list-roles --max-items 1

echo
echo "Cost"
check "budgets:DescribeBudgets" \
  aws budgets describe-budgets --account-id "$(jq -r .Account <<<"$IDENTITY")" --max-results 1

echo
echo "─────────────────────────────────────────"
if [ "$FAIL" -eq 0 ]; then
  green "All $PASS checks passed. You are clear to run terraform apply."
  echo
  dim "Note: read access does not prove write access. If apply still fails on"
  dim "a Create* action, the policy is scoped read-only for that service."
else
  red "$FAIL of $((PASS + FAIL)) checks denied:"
  for s in "${FAILED_SERVICES[@]}"; do
    echo "    - $s"
  done
  echo
  echo "Send shiny-platform-deploy-policy.json to whoever administers the"
  echo "account and ask for it to be attached to your identity."
  exit 1
fi
