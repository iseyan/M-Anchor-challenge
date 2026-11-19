# Rscript tests/test_primary_glmm.R <path to analysis/primary_glmm.R>   (prints "ok" or stops)
args <- commandArgs(trailingOnly = TRUE)
source(args[1])
set.seed(20261123)

design <- function() {
  g <- expand.grid(type = 1:10, sit = 1:5, form = 1:6, sender = c("qwen", "gemma"), rep = 1:3,
                   condition = c("C3", "GC", "C4"), stringsAsFactors = FALSE)
  g$case_type <- paste0("AT", g$type); g$situation <- paste(g$case_type, g$sit, sep = "|")
  g$case_id <- paste(g$situation, g$form); g$trial_id <- paste(g$case_id, g$sender, g$rep)
  g$format <- paste0("F", g$form); g$label <- "attack"
  g$stratum <- ifelse(g$type <= 3 | (g$type == 4 & g$sit <= 3), names(STRATUM_WEIGHTS)[1], names(STRATUM_WEIGHTS)[2])
  g
}

# 1. known fixed effects, small random effects: the weighted contrast recovers the truth
g <- design()
beta <- c(qwen = -2.0, gemma = -1.0)                     # C4 - C3 log-odds in the filtered stratum
shift <- c(qwen = 0, gemma = 0)                           # exposed stratum: no effect
re_i <- rnorm(length(unique(g$trial_id)), 0, .3)[match(g$trial_id, unique(g$trial_id))]
eff <- ifelse(g$condition == "C4", ifelse(g$stratum == names(STRATUM_WEIGHTS)[2], beta[g$sender], shift[g$sender]), 0)
g$mis_propagation <- rbinom(nrow(g), 1, plogis(0.8 + re_i + eff))
g$attempt <- rbinom(nrow(g), 1, plogis(1.0 + re_i + ifelse(g$condition == "C4", -1, 0)))
truth_a <- sum(STRATUM_WEIGHTS * c(0, mean(beta)))        # exposed 0, filtered mean(beta)
r <- primary_test(g, PRIMARY[[1]])
stopifnot(identical(r$method, "glmm_wald"))
if (abs(r$log_or - truth_a) > 3 * r$se) stop(sprintf("H4a estimate %.3f far from truth %.3f (se %.3f)", r$log_or, truth_a, r$se))
rb <- primary_test(g, PRIMARY[[2]])
if (abs(rb$log_or - (-1)) > 3 * rb$se) stop(sprintf("H4b estimate %.3f far from -1 (se %.3f)", rb$log_or, rb$se))

# 2. complete separation in one sender x stratum cell -> not estimable before fitting
g2 <- g
g2$mis_propagation[g2$condition == "C4" & g2$sender == "qwen" & g2$stratum == names(STRATUM_WEIGHTS)[2]] <- 0
r2 <- primary_test(g2, PRIMARY[[1]])
stopifnot(identical(r2$method, "not_estimable"), identical(r2$reason, "separation"))
# the same zero cell does not block the unstratified attempt contrast
stopifnot(identical(primary_test(g2, PRIMARY[[2]])$method, "glmm_wald"))

# 3. Holm keeps a not estimable test in the family with p = 1
res <- holm_primary(list(list(method = "glmm_wald", p = .01, log_or = -1),
                         list(method = "not_estimable"),
                         list(method = "glmm_wald", p = .02, log_or = 1)))
stopifnot(isTRUE(all.equal(res[[1]]$p_holm, .03)), is.na(res[[2]]$p_holm), isTRUE(all.equal(res[[3]]$p_holm, .04)))
stopifnot(res[[1]]$decision == "significant_reduction", res[[2]]$decision == "not_estimable",
          res[[3]]$decision == "significant_increase")
cat("ok\n")
