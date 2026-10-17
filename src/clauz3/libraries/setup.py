"""Operator-only selection of trusted libraries before launching an agent."""

from pathlib import Path

from clauz3.libraries.discovery import catalog, global_catalog, installed_domains
from clauz3.libraries.install import install_library

FILE_LIBRARIES = ("filesystem",)


def choose_libraries(project: Path) -> bool:
    """Return False on cancellation, True after selection (possibly empty)."""
    print("Choose libraries to install into this project:")
    print("  1. File work (recommended): filesystem")
    print("  2. File work + Git/GitHub: also git, gh (requires those CLIs)")
    print("  3. Choose individual bundled or global libraries")
    print("  4. Continue with currently installed tools only")
    print("  q. Cancel")
    try:
        while True:
            selection = input("Selection [1]: ").strip().lower() or "1"
            if selection in {"1", "2", "3", "4", "q"}:
                break
            print("Choose 1, 2, 3, 4, or q.")
        if selection == "q":
            return False
        if selection == "4":
            return True
        if selection in {"1", "2"}:
            names = FILE_LIBRARIES + (("git", "gh") if selection == "2" else ())
            chosen = [
                lib
                for lib in catalog()
                if lib.source in {f"stdlib:{name}" for name in names}
            ]
        else:
            choices = [lib for lib in catalog() if lib.visibility == "bundled"]
            choices += global_catalog()
            for index, library in enumerate(choices, 1):
                print(f"  {index}. {library.source}: {library.description}")
                if library.dependencies:
                    print(
                        f"     Includes dependencies: {', '.join(library.dependencies)}"
                    )
                if library.requirements:
                    print(f"     Requires: {', '.join(library.requirements)}")
            while True:
                answer = input("Numbers to install (comma-separated; blank cancels): ")
                if not answer.strip():
                    return False
                try:
                    indices = list(
                        dict.fromkeys(int(n.strip()) for n in answer.split(","))
                    )
                    if any(index < 1 or index > len(choices) for index in indices):
                        raise ValueError
                except ValueError:
                    print("Enter numbers from the list, separated by commas.")
                    continue
                chosen = [choices[index - 1] for index in indices]
                break
    except EOFError:
        return False

    for library in chosen:
        if all(domain in installed_domains(project) for domain in library.domains):
            print(f"Keeping existing project library: {library.source}")
            continue
        for result in install_library(library.source, into=project):
            for domain in result.domains:
                print(f"Installed: {domain}")
    return True
