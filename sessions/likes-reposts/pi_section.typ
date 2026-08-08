== Inter-Action Time
<sec-cal-interaction>

The `user_inter_action` is ---arguably--- the most crucial parameter in the
simulation. It governs the time between consecutive posts a user sees on their
timeline, i.e. how many posts the user is exposed to during a session.
Paradoxically, it cannot be directly measured from the Firehose (see
@apx-sessions-dataset), as it records actions, not passive views. This section
explains the rationale for the chosen value and the data used to anchor it.

We model the inter-action time as $"Exp"(lambda)$. Two arguments support this
choice.

The first is experiential. A user browsing a timeline almost never reads every
post in full: they skim the grand majority and linger on a few. This pattern
---many short gaps and a long, thin tail of longer ones--- is the hallmark of
an exponential distribution.

The second is structural: if the sequence of posts appearing on a user's
timeline forms a Poisson process, the inter-arrival times are exponentially
distributed and memoryless #todo[reference a Poisson process]. Memorylessness
is reasonable here: the time a user has already spent on the current post
carries no information about how long they will spend on the next one. Each
post is an independent decision point.

What is a plausible value for the mean $1/lambda$, the average dwell time per
post? Viewport-dwell and eye-tracking studies of feed browsing place the
typical dwell per item at roughly 1.5--3 seconds
#todo[cite dwell-time reference]. We therefore adopt $1/lambda = 3$ seconds
per post, i.e. the user scrolls past about 20 posts per minute. The data let
us sanity-check this choice (see @sec-cal-policy), and @fig-pi-sensitivity
shows that our conclusions are robust to the exact value.

In conclusion, the estimation `user_inter_action` $~ "Exp"(1/3)$ is
reasonable both experientially and structurally, as we will see in the next
section.

== User Policy $pi$
<sec-cal-policy>

The user policy is, together with `user_inter_action`
(@sec-cal-interaction), the other crucial quantity we cannot estimate
directly, and for the same reason: we do not know how many posts the user was
exposed to. Unlike the latter, however, $pi$ can be derived once
`user_inter_action` is fixed.

Per simulation design, we assume $pi$ is homogeneous across users, so we drop
the 16 pairs of families found when deducing the sessions (see
@sec-cal-acrossuser) and treat all sessions equally.

The idea is simple. Under `user_inter_action` $~ "Exp"(1/3)$, a session of
duration $t$ seconds exposes the user to $t slash 3$ posts on average. Counting
likes and reposts per session therefore yields the policy probabilities:

$
  pi_"like" = frac(|{"likes"}|, T slash 3), quad
  pi_"repost" = frac(|{"reposts"}|, T slash 3), quad
  pi_"ignore" = 1 - pi_"like" - pi_"repost",
$

where $T$ is the total session time. Zero-duration sessions (isolated events,
40.7% of sessions) are excluded: they contribute no observable exposure and
hold only 6% of all engagements. From the remaining 26.6M sessions
($T approx 6.11 times 10^9$ s, mean duration 229.5 s), we count
148.5M likes (85.9% of engagements) and 24.4M reposts (14.1%), giving:

$
  pi_"ignore" approx 91.5% quad pi_"like" approx 7.3% quad pi_"repost" approx 1.2%
$

For the simulation's JSON `user_policy.categorical.weights` field, this
translates to `[0.915, 0.073, 0.012]` corresponding to
`["ignore", "like", "repost"]`.

Two consistency remarks. First, the data bound the dwell time from above:
$pi_"like" <= 1$ requires $s <= T slash |{"likes"}| approx 41$ s per post,
and any value beyond a few seconds would imply an implausibly high engagement
rate --- the assumed 3 s sits comfortably inside the plausible range. Second,
$pi$ is exactly linear in the assumed dwell time $s$;
@fig-pi-sensitivity plots this dependence on $s in [1, 4]$ s per post: across
the whole plausible range, users ignore around 90% of what they see, so the
qualitative behaviour of the simulation does not hinge on the exact value of
$1 slash lambda$.

#figure(
  image("pi_sensitivity.png", width: 80%),
  caption: [Sensitivity of the user policy $pi$ to the assumed dwell time
    $s$ (seconds per post). Zero-duration sessions excluded. The dashed line
    marks the chosen value $s = 3$ s per post.],
) <fig-pi-sensitivity>
