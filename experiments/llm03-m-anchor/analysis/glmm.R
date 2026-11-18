# Step 2 of the analysis (analysis-plan-v1.md, sections 3, 4 and 6.5-6.6): GLMM (logit) on attack episodes,
# model-averaged contrasts, Holm over the three primary tests, the pre-specified handling of estimation
# problems (3.3), the sensitivity comparison (4), exploratory contrasts, interaction tests and variance
# components on the variance scale.
#
# usage: Rscript analysis/glmm.R <out_dir> [--dev-quick]   (reads <out_dir>/episodes.csv written by prep.py)
#
# Every section is computed inside its own error handler, so a failure in a secondary output is recorded
# and the primary results are still written. glmm_results.json (everything except the variance-component
# intervals) is written first; the variance components, whose bootstrap can take long, follow in
# glmm_varcomp.json, rewritten after each outcome. --dev-quick (dev split only) cuts the bootstrap to 20 draws
# for trying the scripts.

suppressPackageStartupMessages({ library(lme4); library(jsonlite) })
args <- commandArgs(trailingOnly = TRUE)
out_dir <- args[1]
prep_info <- fromJSON(file.path(out_dir, "prep_info.json"))
dev_quick <- "--dev-quick" %in% args
if (dev_quick && prep_info$split != "dev") stop("refused: --dev-quick is only for the dev split")
d <- read.csv(file.path(out_dir, "episodes.csv"), stringsAsFactors = FALSE)
atk <- subset(d, label == "attack")
atk$condition <- factor(atk$condition, levels = c("C3", "C1", "GC", "C4"))
atk$sender <- factor(atk$sender)
senders <- levels(atk$sender)

ctrl <- glmerControl(optimizer = "bobyqa", optCtrl = list(maxfun = 2e5))
f_full <- function(y) as.formula(paste(y, "~ condition * sender + (1 | case_type) + (1 | case_type:case_id) + (1 | trial_id)"))
f_step2 <- function(y) as.formula(paste(y, "~ condition * sender + (1 | case_type:case_id) + (1 | trial_id)"))
BOOT_NSIM <- if (dev_quick) 20 else 500; BOOT_SEED <- 20261119   # variance-component intervals when profiling fails (plan 6.6)
BAD_MSG <- "failed to converge|unable to evaluate scaled gradient|nearly unidentifiable|degenerate|convergence code"

safely <- function(expr) tryCatch(expr, error = function(e) list(method = "not_estimable", error = conditionMessage(e)))

# Convergence: optimizer return code 0 and no lme4 convergence message or warning (singular fits are kept).
fit_glmm <- function(y, formula_fun) {
  warns <- character(0)
  fit <- withCallingHandlers(
    tryCatch(glmer(formula_fun(y), data = atk, family = binomial, nAGQ = 1, control = ctrl), error = function(e) e),
    warning = function(w) { warns <<- c(warns, conditionMessage(w)); invokeRestart("muffleWarning") })
  if (inherits(fit, "error")) return(list(ok = FALSE, warnings = c(warns, conditionMessage(fit))))
  conv <- fit@optinfo$conv
  msgs <- c(warns, conv$lme4$messages)
  ok <- identical(as.numeric(conv$opt), 0) && !any(grepl(BAD_MSG, msgs, ignore.case = TRUE))
  list(ok = ok, fit = fit, optimizer_code = conv$opt, warnings = msgs, singular = isSingular(fit))
}

# Plan 3.3 steps 1-2: the full model, then without (1 | case_type).
fit_with_fallback <- function(y) {
  a <- fit_glmm(y, f_full)
  if (a$ok) return(c(a, step = "1_full"))
  b <- fit_glmm(y, f_step2)
  if (b$ok) return(c(b, step = "2_without_case_type", step1_warnings = list(a$warnings)))
  list(ok = FALSE, step = "not_converged", warnings = list(step1 = a$warnings, step2 = b$warnings))
}

grid <- expand.grid(condition = levels(atk$condition), sender = senders)
grid$condition <- factor(grid$condition, levels(atk$condition)); grid$sender <- factor(grid$sender, senders)
xrow <- function(fit, cond, snd) {
  X <- model.matrix(~ condition * sender, data = grid)[, names(fixef(fit)), drop = FALSE]
  X[grid$condition == cond & grid$sender == snd, ]
}

# contrast of condition b against a (b - a on the log-odds scale); snd = NULL -> equal-weight mean over senders
contrast <- function(fit, a, b, snd = NULL) {
  use <- if (is.null(snd)) senders else snd
  L <- Reduce(`+`, lapply(use, function(s) xrow(fit, b, s) - xrow(fit, a, s))) / length(use)
  est <- sum(L * fixef(fit)); se <- sqrt(as.numeric(t(L) %*% as.matrix(vcov(fit)) %*% L))
  z <- est / se
  list(method = "glmm_wald", log_or = est, se = se, or = exp(est), or_ci95 = exp(est + c(-1, 1) * qnorm(0.975) * se),
       z = z, p = 2 * pnorm(-abs(z)))
}

# Plan 3.3 step 3: in either compared condition, some sender's values are all 0 or all 1
separation_cells <- function(y, a, b, snd = NULL) {
  use <- if (is.null(snd)) senders else snd
  out <- character(0)
  for (cn in c(a, b)) for (s in use) {
    v <- atk[atk$condition == cn & atk$sender == s, y]
    if (length(v) && (all(v == 0) || all(v == 1))) out <- c(out, paste0(s, "|", cn, "=", mean(v)))
  }
  out
}

fits <- list()
get_fit <- function(y) { if (is.null(fits[[y]])) fits[[y]] <<- fit_with_fallback(y); fits[[y]] }

# Plan 3.3 step 3 (revised before the freeze): no substitute test. A contrast that the GLMM cannot estimate is
# reported as not estimable; the case-cluster bootstrap rate difference (boot_desc.py) describes it.
test <- function(y, a, b, snd = NULL) {
  sep <- separation_cells(y, a, b, snd)
  if (length(sep)) return(list(method = "not_estimable", reason = "separation", cells = sep))
  ft <- get_fit(y)
  if (!ft$ok) return(list(method = "not_estimable", reason = "glmm_not_converged"))
  c(contrast(ft$fit, a, b, snd), glmm_step = ft$step, singular = ft$singular)
}

rates <- function(y, conds) {
  setNames(lapply(senders, function(s) setNames(lapply(conds, function(cn)
    mean(atk[atk$condition == cn & atk$sender == s, y])), conds)), senders)
}

holm_with_unestimable <- function(p) {  # a not estimable test keeps its place in the family with p = 1
  p.adjust(ifelse(is.na(p), 1, p), method = "holm")
}
pval <- function(r) if (identical(r$method, "glmm_wald")) r$p else NA_real_

primary_spec <- list(
  list(id = "H4a", outcome = "mis_propagation", a = "C3", b = "C4"),
  list(id = "H4b", outcome = "attempt", a = "C3", b = "C4"),
  list(id = "H4prime", outcome = "mis_propagation", a = "GC", b = "C4"))

primary <- lapply(primary_spec, function(p) c(p, safely(test(p$outcome, p$a, p$b))))
p_raw <- sapply(primary, pval)
p_holm <- holm_with_unestimable(p_raw)
for (i in seq_along(primary)) {
  primary[[i]]$p_holm <- if (is.na(p_raw[i])) NA else p_holm[i]
  primary[[i]]$decision <- if (is.na(p_raw[i])) "not_estimable" else if (p_holm[i] < 0.05) "significant" else "not_significant"
}

# Plan 4: the sensitivity test is compared on the same footing as the primary decision: its p value takes the
# place of the matching primary p value in the family of three, Holm is recomputed, and the resulting
# decision and direction are compared with the primary Holm decision and direction.
sens_spec <- list(list(id = "H4a_not_sent_counted", a = "C3", b = "C4", primary_index = 1),
                  list(id = "H4prime_not_sent_counted", a = "GC", b = "C4", primary_index = 3))
sensitivity <- lapply(sens_spec, function(p) {
  r <- c(p, outcome = "mis_propagation_if_not_sent_counted", safely(test("mis_propagation_if_not_sent_counted", p$a, p$b)))
  prim <- primary[[p$primary_index]]
  ps <- pval(r)
  if (is.na(ps) || prim$decision == "not_estimable") {
    r$p_holm_substituted <- NA; r$decision <- "not_estimable"; r$same_conclusion_as_primary <- NA
    return(r)
  }
  pp <- p_raw; pp[p$primary_index] <- ps
  r$p_holm_substituted <- holm_with_unestimable(pp)[p$primary_index]
  r$decision <- if (r$p_holm_substituted < 0.05) "significant" else "not_significant"
  r$same_conclusion_as_primary <- (r$decision == prim$decision) && (sign(r$log_or) == sign(prim$log_or))
  r
})

per_sender <- list()
for (p in primary_spec) for (s in senders)
  per_sender[[length(per_sender) + 1]] <- c(p, sender = s, safely(test(p$outcome, p$a, p$b, s)))

exploratory <- list(
  c(id = "C1_vs_C3_mis_propagation", outcome = "mis_propagation", a = "C3", b = "C1", safely(test("mis_propagation", "C3", "C1"))),
  c(id = "C1_vs_C3_attempt", outcome = "attempt", a = "C3", b = "C1", safely(test("attempt", "C3", "C1"))),
  c(id = "GC_vs_C4_attempt", outcome = "attempt", a = "GC", b = "C4", safely(test("attempt", "GC", "C4"))),
  c(id = "receiver_false_approval_C3_vs_C4", outcome = "receiver_false_approval", a = "C3", b = "C4",
    safely(test("receiver_false_approval", "C3", "C4"))),
  c(id = "receiver_false_approval_GC_vs_C4", outcome = "receiver_false_approval", a = "GC", b = "C4",
    safely(test("receiver_false_approval", "GC", "C4"))))

interaction <- lapply(c("mis_propagation", "attempt"), function(y) safely({
  ft <- get_fit(y)
  if (!ft$ok) stop("glmm_not_converged")
  warns <- character(0)
  add <- withCallingHandlers(update(ft$fit, . ~ . - condition:sender),
                             warning = function(w) { warns <<- c(warns, conditionMessage(w)); invokeRestart("muffleWarning") })
  a <- anova(add, ft$fit)
  list(outcome = y, method = "lrt_condition_x_sender", chisq = a$Chisq[2], df = a$Df[2], p = a$`Pr(>Chisq)`[2],
       additive_model_optimizer_code = add@optinfo$conv$opt, additive_model_warnings = c(warns, add@optinfo$conv$lme4$messages))
}))

out <- list(
  software = list(R = R.version.string, lme4 = as.character(packageVersion("lme4")),
                  Matrix = as.character(packageVersion("Matrix"))),
  n_attack_episodes = nrow(atk), senders = senders,
  observed_rates = list(mis_propagation = rates("mis_propagation", levels(atk$condition)),
                        attempt = rates("attempt", levels(atk$condition))),
  primary = primary, sensitivity = sensitivity, per_sender = per_sender, exploratory = exploratory,
  interaction = interaction,
  variance_components = "see glmm_varcomp.json",
  model_fits = lapply(fits, function(f) list(ok = f$ok, step = f$step, optimizer_code = f$optimizer_code,
                                              singular = f$singular, warnings = f$warnings)),
  note = paste("Contrasts are b - a on the log-odds scale (or < 1 means b is lower); model-averaged contrasts weight",
               "the two senders equally. A not estimable contrast has no p value and enters Holm with p = 1."))
writeLines(toJSON(out, auto_unbox = TRUE, pretty = TRUE, digits = NA, na = "null", null = "null"),
           file.path(out_dir, "glmm_results.json"))
cat("written", file.path(out_dir, "glmm_results.json"), "\n")

# Plan 6.6: variance components on the variance scale. confint.merMod returns intervals for standard
# deviations; the variance-scale interval squares both ends (monotone for sd >= 0). Profile likelihood first;
# if profiling fails, a parametric bootstrap percentile interval (BOOT_NSIM, BOOT_SEED).
varcomp_one <- function(y) safely({
  ft <- fits[[y]]
  if (!ft$ok) stop("glmm_not_converged")
  vc <- as.data.frame(VarCorr(ft$fit))
  ci_warn <- character(0)
  ci <- withCallingHandlers(
    tryCatch(confint(ft$fit, parm = "theta_", method = "profile", oldNames = FALSE), error = function(e) conditionMessage(e)),
    warning = function(w) { ci_warn <<- c(ci_warn, conditionMessage(w)); invokeRestart("muffleWarning") })
  ci_method <- "profile"; profile_error <- NULL; boot_warn <- character(0)
  if (is.character(ci)) {  # profile failed (e.g. a component on the boundary): parametric bootstrap, percentile
    profile_error <- ci; ci_method <- "parametric_bootstrap_percentile"
    set.seed(BOOT_SEED)
    ci <- withCallingHandlers(
      tryCatch(confint(ft$fit, parm = "theta_", method = "boot", nsim = BOOT_NSIM, boot.type = "perc", oldNames = FALSE),
               error = function(e) conditionMessage(e)),
      warning = function(w) { boot_warn <<- c(boot_warn, conditionMessage(w)); invokeRestart("muffleWarning") })
    if (is.character(ci)) ci_method <- "not_estimable"
  }
  comps <- lapply(seq_len(nrow(vc)), function(i) {
    e <- list(group = vc$grp[i], variance = vc$vcov[i], sd = vc$sdcor[i])
    if (!is.character(ci)) {
      row <- which(rownames(ci) == paste0("sd_(Intercept)|", vc$grp[i]))
      if (length(row) == 1) {
        e$ci95_sd <- unname(ci[row, ]); e$ci95_variance <- unname(ci[row, ])^2
        e$parm <- rownames(ci)[row]
      }
    }
    e
  })
  list(outcome = y, glmm_step = ft$step, singular = ft$singular, ci_method = ci_method, components = comps,
       profile_error = profile_error, profile_warnings = ci_warn,
       bootstrap = if (ci_method != "profile") list(nsim = BOOT_NSIM, seed = BOOT_SEED,
                                                   error = if (is.character(ci)) ci else NULL,
                                                   warnings = unique(boot_warn)) else NULL)
})

varcomp <- list()
for (y in names(fits)) {
  varcomp[[length(varcomp) + 1]] <- varcomp_one(y)
  writeLines(toJSON(list(dev_quick = dev_quick, components = varcomp, done = length(varcomp), of = length(fits)),
                    auto_unbox = TRUE, pretty = TRUE, digits = NA, na = "null", null = "null"),
             file.path(out_dir, "glmm_varcomp.json"))
  cat("variance components:", y, "\n")
}
