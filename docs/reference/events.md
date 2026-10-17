# Events

A relation built with `effect(...)` covers calls to named trusted functions.
That is enough while each resource has one function, but several tools often act
on the same resource. `read_file`, `grep` and `copy` all read files. A policy
written over `effect(read_file)` says nothing about the other two, and every new
tool opens a gap in every existing policy.

Events separate *what a tool does* from *which tool did it*. A trusted function
declares the events it performs, and contracts are written over events. A
contract then covers every tool that performs the event, including tools added
after the contract was written.

## Declaring events

An event class subclasses `clauz3.events.Event`. Its fields are declared like a
dataclass, and the class becomes a frozen dataclass:

```python
from clauz3.events import Event


class FsEvent(Event):
    path: str


class Read(FsEvent): ...


class Write(FsEvent): ...


class Delete(Write): ...
```

Subclassing arranges events into a hierarchy. A relation over a class covers
its subclasses, so here a contract over `Write` also covers `Delete`.

## Declaring what a tool performs

`@performs` goes directly above the `def`, below `@clauz3.trusted`:

```python
import clauz3
from clauz3.events import performs


@clauz3.trusted
@performs(lambda path: Read(path))
def read_file(path: str) -> str: ...


@clauz3.trusted
@performs(lambda src, dest: [Read(src), Write(dest)])
def copy(src: str, dest: str) -> None: ...


@clauz3.trusted
@performs(lambda src, dest: [Read(src), Delete(src), Write(dest)])
def move(src: str, dest: str) -> None: ...
```

The builder's parameters name the tool's parameters, and it may take any subset
of them, in any order. It returns one event or a list of them. Each event field
must be one of those parameters; a literal or computed value is refused when the
trusted layer is imported, as is a parameter the tool does not have, one with a
default, or a variadic one. Declare every event in one `@performs`: a second
one stacked on the same function is refused rather than replacing the first.

At prove time, each reachable call to the tool is read as these events, with
the call's arguments in place. An event inherits the call's reachability and
loop quantifiers, so a call under a branch or inside a `for` loop performs its
events exactly where the call happens.

`@performs` is a trusted-layer declaration, refused in agent-authored source
like `@clauz3.trusted`. It is also trusted in the other sense: a tool that
performs an event it does not declare is invisible to contracts over that
event. Review it with the implementation it describes.

## Contracts over events

An event class is a relation. It has the same queries as `effect(...)`:
`all`, `where`, `empty`, `count`, `sum` and `distinct`.

```python
from clauz3.spec import ContractSpec, contract


@contract
def never_read_under(prefix: str) -> ContractSpec:
    return Read.all(lambda e: not e.path.startswith(prefix))


@contract
def read_only() -> ContractSpec:
    return Write.empty()
```

`never_read_under("/repo/PRECIOUS.md")` now refuses `read_file`, `grep` and a
`copy` or `move` from that file. Moving the file and reading it under a new
name is refused at the move, because a move reads its source.

A contract over an event that no trusted effect of the proof's layer performs
is refused: it would hold vacuously, typically because a tool lost its
`@performs`.

A predicate may read the event's fields. A relation over a base class offers
the base class's fields. Event fields cannot be named after the query methods
(`all`, `count` and so on).

Tool-specific policies can still use `effect(...)` alongside events. In
`stdlib:filesystem`, `only_pattern` constrains `grep`'s own `pattern` argument,
which no event carries.

## Relation to fluents

`@performs` has the same shape as a fluent's `@effect`: a builder that mirrors
the tool's parameters and returns declarations. Events are a bag; their order
does not matter. Fluents fold updates in program order to describe the state
after the program runs. See [Fluents](fluents.md).

## Example

`stdlib:filesystem` is built this way. See its
[README](../stdlib/filesystem.md) for the event hierarchy and the effects that
perform each event.
