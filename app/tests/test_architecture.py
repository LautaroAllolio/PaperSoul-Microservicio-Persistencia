"""Verificación mecánica de la regla de dependencias entre capas (SPEC.md §6, SC-13).

Este módulo no prueba comportamiento: prueba **estructura**. Lee el árbol de
`app/` con `ast` y falla si algún módulo importa algo que la arquitectura prohíbe.

## Por qué un test y no una convención

Una regla de dependencias escrita en el `README` o en un comentario se respeta
exactamente hasta el día en que alguien tiene prisa. Lo que la sostiene es un
check que falla en el CI. `SPEC.md` §6 la declara "verificada mecánicamente" y este
fichero es esa verificación.

## Qué se verifica y qué no

Se verifican **imports directos**, que es lo que la flecha del diagrama de
`SPEC.md` §6 significa:

```
api  ──►  services  ──►  repositories (ABC)
 │            │                  │
 │            └──► schemas        └──► models  ──► beanie
 └──►  schemas, exceptions, core
```

El grafo ** transitivo** no se comprueba, y conviene decir por qué en voz alta en
lugar de dejar que parezca más fuerte de lo que es. `app/schemas/mappers.py` traduce
entre el modelo de persistencia y los DTOs, así que la cadena
`services → schemas.mappers → models.pdf_document → beanie` existe y es
intencionada: el mapper es, por definición, el puente entre las dos fronteras. Una regla
transitiva que prohibiera esa cadena obligaría a duplicar los DTOs o a mover el
mapper, y haría el diseño peor para que el test se viera más estricto.

Lo que sí se verifica, y es lo que rompía de verdad, es que **nadie llegue a la
capa de datos desde la capa de presentación saltándose el servicio**: el día que un
endpoint importe un repositorio para saltarse la lógica de negocio, este test falla.

## Uso

```bash
uv run pytest app/tests/test_architecture.py -v
```

No requiere Docker: sólo lee ficheros del disco.
"""

import ast
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent
PACKAGE = "app"

#: Infraestructura de persistencia. Su presencia en una capa que no debe conocer la
#: base de datos es exactamente lo que SPEC.md §6 prohíbe con `services → beanie`.
PERSISTENCE_INFRASTRUCTURE = ("beanie", "motor", "pymongo", "bson")


@dataclass(frozen=True, slots=True)
class Violation:
    """Una importación prohibida, con el dato suficiente para localizarla y corregirla.

    `line` está para que el mensaje de fallo apunte al sitio exacto, y no a «el
    fichero entero». `importer` y `imported` juntos dicen por qué está mal: con sólo
    uno de los dos el mensaje obliga a abrir el fichero para entender el fallo.
    """

    importer: str
    imported: str
    line: int
    rule: str

    def __str__(self) -> str:
        return f"{self.rule}\n    {self.importer}:{self.line} importa {self.imported!r}"


@dataclass(frozen=True, slots=True)
class ForbiddenImport:
    """Una regla: qué no puede importar qué."""

    #: Paquete bajo vigilancia, relativo a `app/`. `""` significa `app/main.py` y
    #: los módulos sueltos de la raíz.
    package: str
    #: Prefijos de módulo prohibidos. Se compara por prefijo de componente
    #: (`app.api` cubre `app.api.v1.documents`), no por subcadena: un `in` ingenuo
    #: daría un falso positivo con un paquete que se llamara `app.apix`.
    forbidden: tuple[str, ...]
    #: Referencia a la cláusula del SPEC que lo motiva. Aparece en el mensaje de
    #: fallo porque «el test falla» no dice por qué debería importar.
    rule: str


#: El grafo de `SPEC.md` §6, traducido a restricciones verificables.
#:
#: Los paquetes ausentes de esta tabla (`app/models`, `app/schemas/pagination.py`)
#: pueden importar lo que necesiten: son capas donde la dependencia hacia abajo es
#: la dirección permitida.
RULES: tuple[ForbiddenImport, ...] = (
    ForbiddenImport(
        package="api",
        forbidden=(
            "app.repositories",
            "app.models",
            *PERSISTENCE_INFRASTRUCTURE,
        ),
        rule=(
            "SPEC.md §6: api -> repositories y api -> models están prohibidas. "
            "La capa de presentación alcanza los datos a través de services; un "
            "endpoint que importe un repositorio o un modelo de persistencia se "
            "saltaría la capa de negocio."
        ),
    ),
    ForbiddenImport(
        package="services",
        forbidden=PERSISTENCE_INFRASTRUCTURE,
        rule=(
            "SPEC.md §6 y SC-13: services -> beanie|motor|pymongo|bson está "
            "prohibida. La capa de negocio habla con el repositorio por su "
            "contrato, nunca con el driver."
        ),
    ),
    ForbiddenImport(
        package="schemas",
        forbidden=("app.services", "app.api"),
        rule=(
            "SPEC.md §6: schemas -> services está prohibida. Los DTO son el "
            "contrato público; depender del servicio los volvería circulares con él."
        ),
    ),
    ForbiddenImport(
        package="repositories",
        forbidden=("app.api", "app.services"),
        rule=(
            "La capa de datos no conoce hacia arriba. Si un repositorio importara "
            "un servicio o un router, la dependencia dejaría de ir en una sola "
            "dirección."
        ),
    ),
    ForbiddenImport(
        package="exceptions",
        forbidden=("fastapi", "app.api", "app.services", "app.repositories", "app.schemas"),
        rule=(
            "Las excepciones de dominio son la capa transversal más interna: las "
            "lanzan los servicios y las atrapan los handlers. No pueden depender "
            "de ninguna de las dos, ni de FastAPI, o el dominio dejaría de ser "
            "transportable."
        ),
    ),
    ForbiddenImport(
        package="core",
        forbidden=("app.api",),
        rule=(
            "El paquete transversal no depende de la capa de presentación: es la "
            "base de la pirámide, no su cima."
        ),
    ),
)


def source_modules() -> list[Path]:
    """Ficheros `.py` de `app/`, fuera de la suite de tests.

    Los tests se excluyen a propósito. Un test **debe** poder montar un
    `PdfDocument` real, importar el repositorio de verdad y comprobar la unicidad
    contra el `dict` del fake; si este módulo los includera, cada test sería
    también una violación de la arquitectura.
    """
    return sorted(
        path
        for path in APP_ROOT.rglob("*.py")
        if "__pycache__" not in path.parts and "tests" not in path.relative_to(APP_ROOT).parts
    )


def module_name(path: Path) -> str:
    """Ruta del fichero como módulo absoluto: `app/api/v1/documents.py` -> `app.api.v1.documents`.

    Se resuelve la ruta antes de relativizar para que el helper acepte también rutas
    relativas. Sin el `resolve`, pasar una ruta relativa lanza `ValueError` en lugar
    de devolver un nombre, que es la forma menos útil de fallar cuando la función se
    está usando desde un test.
    """
    relative = path.resolve().relative_to(APP_ROOT.parent).with_suffix("")
    return ".".join(relative.parts)


def imports_of(path: Path) -> Iterator[tuple[str, int]]:
    """Módulos importados por `path`, con la línea del `import`.

    Se recogen tanto `import x` como `from x import y`. Los imports relativos se
    resuelven al nombre absoluto del paquete, porque `from .base import Page` en
    `app/repositories/pdf_repository.py` significa `app.repositories.base`, y sin
    resolverlo la regla no vería nada dentro del propio paquete.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name, node.lineno
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                package = module_name(path).split(".")[: -node.level]
                base = ".".join([*package, base] if base else package)
            if base:
                yield base, node.lineno


def matches(module: str, prefix: str) -> bool:
    """`module` es `prefix` o algo dentro de él, comparado por componentes.

    `app.api` cubre `app.api.v1.documents`; `app.api` **no** cubre `app.apix`, que
    es otro paquete. Un `startswith` simple confundiría los dos.
    """
    return module == prefix or module.startswith(f"{prefix}.")


def find_violations(modules: Iterable[Path], rules: Iterable[ForbiddenImport]) -> list[Violation]:
    """Aplica las reglas a los módulos dados y devuelve todas las violaciones.

    Se devuelven todas y no se corta en la primera: quien arregla un incumplimiento
    de arquitectura quiere la lista entera de una vez, no descubrir los siguientes
    uno a uno.
    """
    rules = tuple(rules)
    violations: list[Violation] = []

    for path in modules:
        name = module_name(path)
        package = ".".join(name.split(".")[1:])
        if not package:
            package = ""
        owner = package.split(".")[0] if package else ""

        for rule in rules:
            if owner != rule.package:
                continue

            for imported, line in imports_of(path):
                if any(matches(imported, forbidden) for forbidden in rule.forbidden):
                    violations.append(
                        Violation(
                            importer=name,
                            imported=imported,
                            line=line,
                            rule=rule.rule,
                        )
                    )

    return violations


def internal_graph(modules: Iterable[Path]) -> dict[str, set[str]]:
    """Grafo de dependencias entre módulos de `app/`, como conjunto de aristas.

    Sólo se recogen aristas entre módulos del proyecto; las bibliotecas de terceros
    se ignoran porque no forman parte de la arquitectura interna y porque su grafo
    es inmenso.
    """
    known = {module_name(path) for path in modules}
    graph: dict[str, set[str]] = {name: set() for name in known}

    for path in modules:
        source = module_name(path)
        for imported, _ in imports_of(path):
            # `imports_of` da el módulo tal cual aparece escrito, que puede ser un
            # antepasado del módulo real (`from app.api.v1 import documents` importa
            # `app.api.v1`). Se busca el punto del arco más cercano al origen: es lo
            # que convierte la arista en algo que se puede seguir leyendo.
            candidates = [imported, *(f"{imported}.{part}" for part in imported.split(".")[1:])]
            for candidate in candidates:
                if candidate in known and candidate != source:
                    graph[source].add(candidate)
                    break

    return graph


def cycles_in(graph: dict[str, set[str]]) -> list[list[str]]:
    """Ciclos del grafo, cada uno como la lista de módulos que lo cierran.

    DFS con pila explícita y conjunto de nodos en el camino. Un ciclo entre capas es
    la forma más Cara de romper la regla de dependencias, porque no se ve en una
    revisión: el fichero A importa B, y B importa A, y cada import parece legítimo
    por separado.
    """
    found: list[list[str]] = []
    seen_cycles: set[frozenset[str]] = set()

    def visit(node: str, path: list[str], on_path: set[str]) -> None:
        for neighbour in sorted(graph.get(node, ())):
            if neighbour in on_path:
                cycle = path[path.index(neighbour) :]
                key = frozenset(cycle)
                if key not in seen_cycles:
                    seen_cycles.add(key)
                    found.append([*cycle, neighbour])
            elif neighbour not in path:
                path.append(neighbour)
                on_path.add(neighbour)
                visit(neighbour, path, on_path)
                on_path.remove(neighbour)
                path.pop()

    for start in sorted(graph):
        visit(start, [start], {start})

    return found


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_the_rule_set_is_not_empty() -> None:
    """Guarda contra una regla borrada por accidente.

    Un `RULES` vacío haría pasar todos los tests de este fichero sin comprobar
    nada, que es el peor resultado posible para un test cuyo único propósito es
    comprobar algo. Cuesta cuatro líneas y evita ese fallo silencioso.
    """
    assert RULES, "RULES está vacío: la suite de arquitectura no comprobaría nada"
    assert all(rule.forbidden for rule in RULES), "una regla no prohíbe nada"
    assert all(rule.rule for rule in RULES), "una regla no explica su motivo"


def test_source_modules_are_discovered() -> None:
    """El recorrido del árbol tiene que encontrar de verdad los módulos de producción.

    Si `source_modules()` devolviera una lista vacía por un cambio en el layout,
    todos los tests de violaciones pasarían sin haber leído un solo fichero. Este
    test hace que eso falle de forma visible.
    """
    names = {module_name(path) for path in source_modules()}

    assert "app.main" in names
    assert "app.api.v1.documents" in names
    assert "app.services.document_service" in names
    assert "app.repositories.pdf_repository" in names


def test_api_layer_does_not_reach_persistence_dependencies() -> None:
    """SC-13, regla `api -> repositories` / `api -> models` (SPEC.md §6).

    Es la regla que este fichero existe para hacer cumplir: los endpoints hablan con
    el servicio, y el servicio con el repositorio. Un `from app.repositories...`
    dentro de `app/api/` significa que hay lógica de persistencia en el router, que
    es exactamente lo que las tres capas existen para evitar.
    """
    violations = find_violations(
        source_modules(),
        [
            rule
            for rule in RULES
            if rule.package == "api"
            and ("app.repositories" in rule.forbidden or "app.models" in rule.forbidden)
        ],
    )

    assert not violations, "\n".join(str(v) for v in violations)


def test_api_layer_does_not_import_persistence_infrastructure() -> None:
    """`api -> beanie|motor|pymongo|bson` (SPEC.md §6).

    Los routers describen HTTP. Que el ODM aparezca en un import significa que la
    capa de presentación ha bajado a tocar la base de datos.
    """
    violations = find_violations(
        source_modules(),
        [
            rule
            for rule in RULES
            if rule.package == "api" and set(PERSISTENCE_INFRASTRUCTURE) & set(rule.forbidden)
        ],
    )

    assert not violations, "\n".join(str(v) for v in violations)


def test_services_layer_does_not_import_the_driver() -> None:
    """SC-13 tal como lo enunció el SPEC: `services -> beanie`.

    Es el criterio de aceptación literal de SC-13. La capa de negocio se prueba
    entera con un fake; si importara el driver, ni el test ni el fake serían
    posibles.
    """
    violations = find_violations(
        source_modules(),
        [rule for rule in RULES if rule.package == "services"],
    )

    assert not violations, "\n".join(str(v) for v in violations)


def test_schemas_layer_does_not_depend_on_the_layers_that_depend_on_it() -> None:
    """`schemas -> services` y `schemas -> api` (SPEC.md §6).

    Los DTOs son la frontera pública. Si un DTO importara el servicio, el servicio
    tendría que importar el DTO para tipar sus retornos, y el contrato público
    quedaría en un ciclo con su propio consumidor.
    """
    violations = find_violations(
        source_modules(),
        [rule for rule in RULES if rule.package == "schemas"],
    )

    assert not violations, "\n".join(str(v) for v in violations)


def test_data_layer_does_not_depend_on_the_layers_above_it() -> None:
    """`repositories -> services` y `repositories -> api`.

    La dependencia va en un solo sentido. Un repositorio que importe el servicio
    invierte el flujo: el servicio dejaría de ser quien decide y el repositorio
    empezaría a decidir cómo se le llama.
    """
    violations = find_violations(
        source_modules(),
        [rule for rule in RULES if rule.package == "repositories"],
    )

    assert not violations, "\n".join(str(v) for v in violations)


def test_domain_exceptions_are_transport_independent() -> None:
    """`exceptions` no depende de FastAPI ni de ninguna capa de aplicación.

    Es lo que permite que el mismo `ResourceNotFoundException` lo lance el servicio
    y lo traduzca el handler sin que el dominio conozca el destino de la excepción.
    """
    violations = find_violations(
        source_modules(),
        [rule for rule in RULES if rule.package == "exceptions"],
    )

    assert not violations, "\n".join(str(v) for v in violations)


def test_cross_cutting_package_does_not_depend_on_presentation() -> None:
    """`core -> api` está prohibido; `core -> repositories` es lo esperado.

    `core/container.py` es el composition root y sí construye repositorios: por eso
    esta regla es sólo sobre `api`. Lo que no puede ocurrir es que un módulo
    transversal necesite un router, que es lo que haría que la base dependiera de la
    cima.
    """
    violations = find_violations(
        source_modules(),
        [rule for rule in RULES if rule.package == "core"],
    )

    assert not violations, "\n".join(str(v) for v in violations)


def test_no_import_cycles_between_modules() -> None:
    """El grafo de imports interno es acíclico.

    Un ciclo no se ve en la revisión del fichero: cada import parece legítimo por
    separado. Se paga en tiempo de ejecución, con errores de import que sólo
    aparecen si se importa el módulo por el orden equivocado.
    """
    modules = source_modules()
    cycles = cycles_in(internal_graph(modules))

    assert not cycles, "\n".join(" -> ".join(cycle) for cycle in cycles)


def test_internal_graph_is_connected_to_the_composition_root() -> None:
    """Todo módulo de `app/` debe ser alcanzable desde `app.main` siguiendo imports.

    Un módulo que nada importa y al que nada importa es código muerto: no está en el
    grafo de ejecución, así que nadie lo ejecuta y sus tests pueden pasar sobre un
    fichero que el servicio real nunca carga.

    Los `__init__.py` quedan fuera del conteo y por un motivo concreto: Python los
    carga implícitamente al importar su paquete, así que ninguno aparece nunca en un
    `import` escrito a mano. Contarlos como huérfanos daría ocho falsos positivos
    (uno por paquete) y enseñaría a ignorar el test.
    """
    graph = internal_graph(source_modules())
    reachable = {"app.main"}
    frontier = ["app.main"]

    while frontier:
        node = frontier.pop()
        for neighbour in graph.get(node, ()):
            if neighbour not in reachable:
                reachable.add(neighbour)
                frontier.append(neighbour)

    orphans = sorted(name for name in set(graph) - reachable if not name.endswith(".__init__"))

    assert not orphans, f"módulos inalcanzables desde app.main: {orphans}"
