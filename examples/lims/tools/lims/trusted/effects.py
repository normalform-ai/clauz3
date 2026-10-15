import clauz3


@clauz3.requires(
    lambda plate, well, volume_ul, reagent: 0 < volume_ul <= 200,
    message="volume_ul must be in (0, 200]",
)
@clauz3.trusted
def pipette(plate: str, well: str, volume_ul: int, reagent: str) -> None:
    """MOCK trusted pipette action.

    Transfers ``volume_ul`` microlitres of ``reagent`` into ``well`` on
    ``plate``. Per-call volume is bounded by the trusted layer; total volume
    per reagent and per-plate scope are agent-stated guarantees.
    """
    pass


@clauz3.requires(
    lambda instrument, plate, hours: 0 < hours <= 24,
    message="hours must be in (0, 24]",
)
@clauz3.trusted
def submit_protocol(instrument: str, plate: str, hours: int) -> None:
    """MOCK trusted instrument submission.

    Books ``instrument`` for ``hours`` to run a protocol on ``plate``.
    Per-call duration is bounded; instrument allowlist and total runtime
    are agent-stated guarantees.
    """
    pass


@clauz3.requires(
    lambda seq, copies: 0 < copies <= 1000,
    message="copies must be in (0, 1000]",
)
@clauz3.trusted
def order_oligo(seq: str, copies: int) -> None:
    """MOCK trusted oligo order.

    Submits an order for ``copies`` of synthesized oligonucleotide ``seq``.
    Sequence content (length, hazard motifs) is an agent-stated guarantee
    and is the surface biosecurity policies attach to.
    """
    pass
