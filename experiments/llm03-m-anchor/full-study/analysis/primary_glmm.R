# Primary tests of the expanded study (analysis-plan-v1-draft.md, section 2), as written in the plan.
#
#   H4(a)  mis_propagation  C3 vs C4   fixed: condition x sender x stratum + format
#   H4(b)  attempt          C3 vs C4   fixed: condition x sender + format
#   H4'    mis_propagation  GC vs C4   fixed: condition x sender x stratum + format
#
# Each contrast is fitted on its own two conditions (attack episodes only):
#   y ~ fixed + (1 | case_type) + (1 | situation) + (0 + cond_b | situation) + (1 | case_id) + (1 | trial_id)
# situation intercept and condition slope are independent terms. Estimation: lme4::glmer, binomial logit,
# Laplace (nAGQ = 1), bobyqa. Steps: (1) full; (2) without (1 | case_type); (3) otherwise not estimable.
# Converged = optimizer code 0 and no lme4 convergence warning or message. Singular fits are kept and
# reported. A contrast is not estimable before fitting when, in either compared condition, some
# sender (x stratum, where the stratum is in the model) cell is all 0 or all 1 (complete separation).
# Contrast: b - a on the log-odds scale, equal weights over the two senders; for mis_propagation also
# fixed stratum weights 108/300 (exposed) and 192/300 (filtered). Format main effects cancel.
# Holm over the three primary tests; a not estimable test stays in the family with p = 1.
#
# usage:  Rscript analysis/primary_glmm.R <analysis_episodes.csv> <out.json>
# The functions are also sourced by the simulation (analysis/sim_glmm.R); keep them side-effect free.

suppressPackageStartupMessages({ library(lme4); library(jsonlite) })

PRIMARY <- list(
  list(id = "H4a", outcome = "mis_propagation", a = "C3", b = "C4", stratified = TRUE),
  list(id = "H4b", outcome = "attempt", a = "C3", b = "C4", stratified = FALSE),
  list(id = "H4prime", outcome = "mis_propagation", a = "GC", b = "C4", stratified = TRUE))
STRATUM_WEIGHTS <- c(admissible_kind_present_for_unconfirmed = 108 / 300,
                     no_admissible_kind_for_any_unconfirmed = 192 / 300)
BAD_MSG <- "failed to converge|unable to evaluate scaled gradient|nearly unidentifiable|degenerate|convergence code"
CTRL <- glmerControl(optimizer = "bobyqa", optCtrl = list(maxfun = 2e5))

contrast_data <- function(d, spec) {
  x <- d[d$label == "attack" & d$condition %in% c(spec$a, spec$b), ]
  x$y <- as.integer(x[[spec$outcome]])
  x$cond <- factor(x$condition, levels = c(spec$a, spec$b))
  x$cond_b <- as.numeric(x$cond == spec$b)
  x$sender <- factor(x$sender)
  x$format <- factor(x$format)
  x$stratum <- factor(x$stratum, levels = names(STRATUM_WEIGHTS))
  x
}

separation_cells <- function(x, spec) {
  keys <- if (spec$stratified) c("cond", "sender", "stratum") else c("cond", "sender")
  m <- aggregate(x$y, by = x[keys], FUN = mean)
  bad <- m[m$x == 0 | m$x == 1, , drop = FALSE]
  if (!nrow(bad)) return(character(0))
  apply(bad, 1, function(r) paste(paste(keys, r[keys], sep = "="), collapse = "|"))
}

fixed_part <- function(spec) if (spec$stratified) "y ~ cond * sender * stratum + format" else "y ~ cond * sender + format"

model_formula <- function(spec, with_type) {
  re <- c(if (with_type) "(1 | case_type)", "(1 | situation)", "(0 + cond_b | situation)", "(1 | case_id)", "(1 | trial_id)")
  as.formula(paste(fixed_part(spec), "+", paste(re, collapse = " + ")))
}

fit_once <- function(x, f) {
  warns <- character(0)
  fit <- withCallingHandlers(
    tryCatch(glmer(f, data = x, family = binomial, nAGQ = 1, control = CTRL), error = function(e) e),
    warning = function(w) { warns <<- c(warns, conditionMessage(w)); invokeRestart("muffleWarning") })
  if (inherits(fit, "error")) return(list(ok = FALSE, warnings = c(warns, conditionMessage(fit))))
  conv <- fit@optinfo$conv
  msgs <- unique(c(warns, conv$lme4$messages))
  ok <- identical(as.numeric(conv$opt), 0) && !any(grepl(BAD_MSG, msgs, ignore.case = TRUE))
  list(ok = ok, fit = fit, optimizer_code = conv$opt, warnings = msgs, singular = isSingular(fit))
}

contrast_vector <- function(fit, x, spec) {
  grid <- expand.grid(cond = levels(x$cond), sender = levels(x$sender), stratum = levels(x$stratum),
                      format = levels(x$format)[1], KEEP.OUT.ATTRS = FALSE)
  for (v in c("cond", "sender", "stratum", "format")) grid[[v]] <- factor(grid[[v]], levels = levels(x[[v]]))
  X <- model.matrix(as.formula(sub("^y ", "", fixed_part(spec))), data = grid)
  X <- X[, names(fixef(fit)), drop = FALSE]
  w_stratum <- if (spec$stratified) STRATUM_WEIGHTS[as.character(grid$stratum)] else 1 / nlevels(x$stratum)
  sign <- ifelse(grid$cond == spec$b, 1, -1)
  w <- sign * (1 / nlevels(x$sender)) * w_stratum
  colSums(X * w)
}

primary_test <- function(d, spec) {
  x <- contrast_data(d, spec)
  out <- list(id = spec$id, outcome = spec$outcome, a = spec$a, b = spec$b, n = nrow(x),
              observed = lapply(split(x$y, list(x$cond, x$sender), sep = "|"), function(v) c(events = sum(v), n = length(v))))
  sep <- separation_cells(x, spec)
  if (length(sep)) return(c(out, method = "not_estimable", reason = "separation", cells = list(sep)))
  attempts <- list()
  for (step in 1:2) {
    r <- fit_once(x, model_formula(spec, with_type = step == 1))
    attempts[[step]] <- list(step = step, ok = r$ok, optimizer_code = r$optimizer_code, singular = r$singular,
                             warnings = r$warnings)
    if (r$ok) {
      L <- contrast_vector(r$fit, x, spec)
      est <- sum(L * fixef(r$fit)); se <- sqrt(as.numeric(t(L) %*% as.matrix(vcov(r$fit)) %*% L))
      vc <- as.data.frame(VarCorr(r$fit))
      return(c(out, method = "glmm_wald", step = step, log_or = est, se = se, or = exp(est),
               or_ci95 = list(exp(est + c(-1, 1) * qnorm(.975) * se)), z = est / se, p = 2 * pnorm(-abs(est / se)),
               singular = r$singular, variance_components = list(setNames(as.list(vc$vcov), paste(vc$grp, vc$var1, sep = ":"))),
               attempts = list(attempts)))
    }
  }
  c(out, method = "not_estimable", reason = "not_converged", attempts = list(attempts))
}

holm_primary <- function(results) {
  p <- vapply(results, function(r) if (identical(r$method, "glmm_wald")) r$p else NA_real_, 0)
  adj <- p.adjust(ifelse(is.na(p), 1, p), method = "holm")
  for (i in seq_along(results)) {
    results[[i]]$p_holm <- if (is.na(p[i])) NA else adj[i]
    results[[i]]$decision <- if (is.na(p[i])) "not_estimable" else if (adj[i] < .05) {
      if (results[[i]]$log_or < 0) "significant_reduction" else "significant_increase"
    } else "not_significant"
  }
  results
}

run_primary <- function(d) holm_primary(lapply(PRIMARY, function(s) primary_test(d, s)))

if (sys.nframe() == 0) {
  args <- commandArgs(trailingOnly = TRUE)
  d <- read.csv(args[1], stringsAsFactors = FALSE)
  res <- run_primary(d)
  out <- list(software = list(R = R.version.string, lme4 = as.character(packageVersion("lme4")),
                              Matrix = as.character(packageVersion("Matrix"))),
              stratum_weights = as.list(STRATUM_WEIGHTS), primary = res)
  writeLines(toJSON(out, auto_unbox = TRUE, pretty = TRUE, digits = NA, na = "null", null = "null"), args[2])
  cat("written", args[2], "\n")
}
