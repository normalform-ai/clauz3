The idea here is to adapt https://deal.readthedocs.io/basic/side-effects.html
and perhaps a subset of python (perhaps corresponding to skylark, but with types),
and to create a system that can be used by AI agents to show contracts alongside
python code they intend to run.

Examples of contracts:

- running this emails nobody except bob@foo and ann@foo
- no one will be emailed twice
- a maximum of $5 will be subtracted from balance
- no files outside ~/sandbox will be touched
- foo.ddb will be read but not written to
- the only table in foo.ddb that will be read is T

I want *static* analysis rather than runtime (which could be
problematic if I want the program to ideally run as one transaction).

For static analysis I am perfectly prepared to work with
the subset of python that deals-z3 supports.

Of course we need to bottom out in some trusted functions
that declare contracts we trust.

Undecided on best way to specify "table" style rules;
e.g. blanket inclusion list for email

There is a previous attempt that got too convoluted here:
../agent-safety-layer.


Some old notes below:

///

deal-solver

general idea: python run in restricted environment, main function
has to declare any side effects and programs called

https://deal.readthedocs.io/basic/side-effects.html

e.g.

@deal.has('sends_email')
def send_email(addr: str, msg: str) -> None:
    ...


note that this means this will fail:

@deal.has()
def main():
  send_email("do_not_email_me@foo.com", ...)

the agent is force to declare a has(...), and a pre-processer
can decide whether to pass through, error, or ask (note:
done in the wrapper not by the agent harness)

minimally, a user can configure one of:

ok(sends_email).
never(sends_email).
ask(sends_email).

the wrapper function will handle the logic.

should_i_ask_user :-
  has(E),
  ask(E).

or maybe better - the agent writes the function AND writes an ask of the user

"I would like permission for: send_email, ... y/N?"

the next level up is declaring conditionals

a system wide config could have:

@deal.has('sends_email')
@deal.pre(...) # not in inclusion list
@deal.pre(...) # not in exclusion list
def send_email(addr: str, msg: str) -> None:
   ...

but maybe better to generally inject:

    inject_pre(f, actively_permitted(...), \+ actively_denied(...))

Note: an inclusion list might be best specified as double negative on actively_denied

these would be specified from a cross-product mixture of:

 - { facts, rules }
 - { environment, per-call }

the agent would abductively reason about a minimal set of per-call rules/facts to ask the
user to temporarily set

e.g.

# I guarantee to never email anyone except...
set(actively_permitted, [...])

note a rule like

denied(sends_email(_,_))

serves the blanket case above

RISK: if agent guesses wrong, then even though the user approved
some things, it may fail at runtime, mid-transaction (e.g. emailed
one person, not the other). It technically doesn't fail, but the intent is
one transaction...
.. in this case, static analysis wins

alternatives include just using prolog as the language...



