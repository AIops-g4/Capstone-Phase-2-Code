# Security Group for ALB
resource "aws_security_group" "alb_sg" {
  name        = "tf-3-ai-engine-alb-sg"
  description = "Security group for internal ALB"
  vpc_id      = aws_vpc.main.id

  ingress {
    from_port   = 8080
    to_port     = 8080
    protocol    = "tcp"
    cidr_blocks = [aws_vpc.main.cidr_block] # Restrict to VPC (Internal)
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# Internal ALB
resource "aws_lb" "internal_alb" {
  name               = "tf-3-ai-engine-alb"
  internal           = true
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb_sg.id]
  subnets            = [aws_subnet.private_1.id, aws_subnet.private_2.id]
}

# Target Group
resource "aws_lb_target_group" "ai_engine_tg" {
  name        = "tf-3-ai-engine-tg"
  port        = 8080
  protocol    = "HTTP"
  vpc_id      = aws_vpc.main.id
  target_type = "ip"

  health_check {
    path                = "/health"
    interval            = 30
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
    matcher             = "200"
  }
}

# Listener
resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.internal_alb.arn
  port              = "8080"
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.ai_engine_tg.arn
  }
}

output "alb_dns_name" {
  description = "The DNS name of the Internal ALB"
  value       = aws_lb.internal_alb.dns_name
}
