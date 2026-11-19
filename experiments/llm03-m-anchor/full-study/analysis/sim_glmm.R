# Stage B of simulation-spec-v1.md. Episode-level generation with the structure of simulate_intervals.py
# (situation, case and initial-evaluation intercepts, situation x condition slopes, shared-uniform
# dependence across conditions), the plan's primary tests (primary_glmm.R run_primary), the rate interval
# with the selected method, and the conditional TOST of decisions.py.
#
# Rscript analysis/sim_glmm.R <params.json> <out.jsonl> <reps> <seed> <method> <workers> <primary_glmm.R>
suppressPackageStartupMessages({ library(jsonlite); library(parallel) })
args <- commandArgs(trailingOnly = TRUE)
params <- fromJSON(args[1]); out <- args[2]; reps <- as.integer(args[3]); seed <- as.integer(args[4])
method <- args[5]; workers <- as.integer(args[6]); source(args[7])
SHARED <- .5; MARGIN <- .03
SENDERS <- c("qwen", "gemma"); CONDS <- params$conditions
mask <- params$mask_exposed                       # jsonlite keeps the JSON index order: [type, situation, format]
stopifnot(identical(dim(mask), c(10L, 5L, 6L)), sum(mask) == 108)

skeleton <- expand.grid(t = 1:10, i = 1:5, f = 1:6, s = 1:2, r = 1:3, c = seq_along(CONDS))
skeleton$case_type <- paste0("AT", skeleton$t)
skeleton$situation <- paste(skeleton$case_type, skeleton$i, sep = "|")
skeleton$case_id <- paste(skeleton$situation, skeleton$f, sep = "|F")
skeleton$trial_id <- paste(skeleton$case_id, SENDERS[skeleton$s], skeleton$r, sep = "|")
skeleton$format <- paste0("F", skeleton$f)
skeleton$sender <- SENDERS[skeleton$s]
skeleton$condition <- CONDS[skeleton$c]
skeleton$label <- "attack"
skeleton$stratum <- ifelse(mask[cbind(skeleton$t, skeleton$i, skeleton$f)] == 1, names(STRATUM_WEIGHTS)[1], names(STRATUM_WEIGHTS)[2])

gen <- function(p) {
  pred <- p$predictor                               # [type, situation, format, sender, condition]
  stopifnot(identical(dim(pred), c(10L, 5L, 6L, 2L, length(CONDS))))
  frame <- matrix(rnorm(50, 0, .5), 10, 5)
  case <- array(rnorm(300, 0, sqrt(p$case_var)), c(10, 5, 6))
  init <- array(rnorm(10 * 5 * 6 * 2 * 3, 0, sqrt(p$trial_var)), c(10, 5, 6, 2, 3))
  slope <- array(rnorm(10 * 5 * length(CONDS)), c(10, 5, length(CONDS))) * rep(p$slope_sd, each = 50)
  k <- skeleton
  eta <- pred[cbind(k$t, k$i, k$f, k$s, k$c)] + frame[cbind(k$t, k$i)] + case[cbind(k$t, k$i, k$f)] +
    init[cbind(k$t, k$i, k$f, k$s, k$r)] + slope[cbind(k$t, k$i, k$c)]
  common <- array(runif(10 * 5 * 6 * 2 * 3), c(10, 5, 6, 2, 3))
  use <- array(runif(10 * 5 * 6 * 2 * 3) < SHARED, c(10, 5, 6, 2, 3))
  u <- ifelse(use[cbind(k$t, k$i, k$f, k$s, k$r)], common[cbind(k$t, k$i, k$f, k$s, k$r)], runif(nrow(k)))
  as.integer(u < plogis(eta))
}

situation_diff <- function(d, y, a, b) {
  m <- aggregate(d[[y]], by = list(t = d$t, i = d$i, s = d$s, cond = d$condition), FUN = mean)
  ma <- m[m$cond == a, ]; mb <- m[m$cond == b, ]
  key <- function(x) paste(x$t, x$i, x$s)
  diff <- mb$x[match(key(ma), key(mb))] - ma$x
  v <- tapply(diff, list(ma$t, ma$i), mean)        # equal sender weights
  v
}

interval90 <- function(v) {
  nt <- nrow(v); ns <- ncol(v); theta <- mean(v)
  if (method == "jackknife_t") {
    w <- v - rowMeans(v); var <- sum(w^2) / (nt^2 * ns * (ns - 1)); q <- qt(.95, nt * (ns - 1))
    return(c(theta - q * sqrt(var), theta + q * sqrt(var), theta))
  }
  m <- if (method == "percentile") ns else ns - 1
  bs <- replicate(999, mean(sapply(seq_len(nt), function(h) mean(v[h, sample.int(ns, m, replace = TRUE)]))))
  c(unname(quantile(bs, c(.05, .95))), theta)
}

tost <- function(pr, ci) {
  if (is.null(pr$p_holm) || is.na(pr$p_holm)) return("not_run_not_estimable")
  if (pr$p_holm < .05) return("not_run_significant")
  if (ci[1] > -MARGIN && ci[2] < MARGIN) "equivalent" else "inconclusive"
}

one_rep <- function(rep) {
  set.seed(seed + rep)
  d <- skeleton
  d$mis_propagation <- gen(params$mis)
  d$attempt <- gen(params$att)
  res <- run_primary(d)
  names(res) <- vapply(res, function(r) r$id, "")
  ci_m <- interval90(situation_diff(d, "mis_propagation", "C3", "C4"))
  ci_a <- interval90(situation_diff(d, "attempt", "C3", "C4"))
  slim <- lapply(res, function(r) list(method = r$method, reason = r$reason, step = r$step, log_or = r$log_or, se = r$se,
                                       p = r$p, p_holm = r$p_holm, decision = r$decision, singular = r$singular))
  list(rep = rep, primary = slim,
       rate_diff = list(H4a = ci_m[3], H4b = ci_a[3]), ci90 = list(H4a = ci_m[1:2], H4b = ci_a[1:2]),
       interval_only_inside = list(H4a = ci_m[1] > -MARGIN && ci_m[2] < MARGIN, H4b = ci_a[1] > -MARGIN && ci_a[2] < MARGIN),
       tost = list(H4a = list(status = sub("^not_run.*", "not_run", tost(res$H4a, ci_m)), detail = tost(res$H4a, ci_m)),
                   H4b = list(status = sub("^not_run.*", "not_run", tost(res$H4b, ci_a)), detail = tost(res$H4b, ci_a))))
}

done <- if (file.exists(out)) vapply(readLines(out), function(l) fromJSON(l)$rep, 0L) else integer(0)
todo <- setdiff(seq_len(reps), done)
chunk <- max(workers, 1) * 2
for (start in seq(1, length(todo), by = chunk)) {
  ids <- todo[start:min(length(todo), start + chunk - 1)]
  res <- mclapply(ids, function(r) tryCatch(one_rep(r), error = function(e) list(rep = r, error = conditionMessage(e))),
                  mc.cores = workers)
  con <- file(out, "a")
  for (x in res) writeLines(toJSON(x, auto_unbox = TRUE, digits = NA, na = "null", null = "null"), con)
  close(con)
  cat(format(Sys.time()), "done", length(done) + start + length(ids) - 1, "of", reps, "\n")
}
