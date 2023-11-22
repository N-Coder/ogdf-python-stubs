import ast
import os
import re
from collections import defaultdict
from textwrap import indent
from typing import Dict, ClassVar, Optional

import sys
from itertools import chain, count
from lxml import etree
import warnings

Element = etree._Element

UNKNOWN_MEMBER_DEFS = set()
UNKNOWN_OTHER_DEFS = set()


class ParsedElement(object):
    INSTANCES: ClassVar[Dict[str, "ParsedElement"]] = {}
    NAMESPACE: ClassVar[Dict[str, "ParsedElement"]] = {}

    def __init__(self, xml: Element, container: Optional["ParsedElement"]):
        self.xml = xml
        self.container = container

        # TODO correctly parse detaileddescription
        brief = xml.find("briefdescription")
        if brief is not None:
            self.brief = "".join(t.strip() for t in brief.itertext())
            self.brief = " ".join(t.strip() for t in self.brief.splitlines())
        id = xml.attrib.get("id", "")
        if id:
            ParsedElement.INSTANCES[id] = self
        self.container = None

    def __eq__(self, other):
        return str(self) == str(other)

    def __hash__(self):
        return hash(str(self))

    def __repr__(self):
        return etree.tostring(self.xml, pretty_print=True).decode()

    @property
    def qualname(self):
        if "qualname" in self.__dict__:
            return self.__dict__["qualname"]
        elif hasattr(self.container, "qualname") and hasattr(self, "name"):
            return "%s.%s" % (self.container.qualname, self.name)
        name = getattr(self, "name", None)
        if not name or not name.startswith("ogdf"):
            print(type(self).__name__, self, "doesn't know its qualname/containing namespace!", file=sys.stderr)
        return name or "???"

    @qualname.setter
    def qualname(self, value):
        self.__dict__["qualname"] = value

    def resolve(self):
        pass

    def check(self):
        for i in count(1):
            val = None
            try:
                val = str(self)
                with warnings.catch_warnings(record=True) as ws:
                    ast.parse(val)
                if ws:
                    print("%s '%s':%s" % (self.__class__.__name__,
                                          self.qualname, indent("\n".join(map(str, ws)), " - ")),
                          file=sys.stderr)
                    print(indent(val, "\t"), file=sys.stderr)
                break
            except Exception as e:
                if hasattr(self, "fix%s" % i):
                    getattr(self, "fix%s" % i)()
                else:
                    print("%s '%s': %s" % (self.__class__.__name__, self.qualname, e), file=sys.stderr)
                    print(val, file=sys.stderr)
                    raise


class Param(ParsedElement):
    def __init__(self, xml: Element, container: Optional[ParsedElement]):
        super().__init__(xml, container)
        name = xml.find("declname")
        if name is not None:
            self.name = name.text
        else:
            self.name = "_"
        self.type = Type(xml.find("type"), container)
        defval = xml.find("defval")
        if defval is not None:
            if str(self.type).startswith("Callable"):
                self.default = "print"
            else:
                self.default = parse_default(defval)
        else:
            self.default = None

        self.check()

    def resolve(self):
        self.type.resolve()

    def fix1(self):
        self.name = "_" + self.name

    def __str__(self):
        parts = [self.name]
        if self.type:
            parts.append(":")
            parts.append(str(self.type))
        if self.default:
            parts.append("=")
            parts.append(self.default)
        return " ".join(parts)


class Template(ParsedElement):
    # TODO
    """
    <detaileddescription><para>
        <parameterlist kind="templateparam">
            <parameteritem>
                <parameternamelist>
                    <parametername>CONTAINER</parametername>
                </parameternamelist>
                <parameterdescription>
                    <para>is the type of node container which is returned.</para>
                </parameterdescription>
            </parameteritem>
        </parameterlist>
    """

    def __init__(self, xml: Element, container: Optional[ParsedElement]):
        super().__init__(xml, container)
        elem = xml.find("declname")
        if elem is not None:
            self.name = elem.text
        else:
            elem = xml.find("type")
            if elem is None:
                elem = xml
            pre, _, suf = "".join(elem.itertext()).strip().partition(" ")
            if pre in ["class", "typename"]:
                self.name = suf
            else:
                self.name = elem.text
        self.name = self.name.strip()
        self.ignore = "enable_if" in self.name

        self.check()

    def fix1(self):
        self.name = "_" + self.name

    def __str__(self):
        if self.ignore:
            return "# %s" % self.name
        else:
            return "{T} = TypeVar('{T}')".format(T=self.name)


class Function(ParsedElement):
    def __init__(self, xml: Element, container: Optional[ParsedElement]):
        super().__init__(xml, container)
        self.name = xml.find("name").text
        self.returnt = Type(xml.find("type"), container)
        self.params = [Param(p, container) for p in xml.iterfind("param")]
        templ = xml.find("templateparamlist")
        if templ is not None:
            self.templ = [Template(param, container) for param in templ.iterfind("param")]
        else:
            self.templ = []
        self.overloaded = False

        self.check()

    def resolve(self):
        self.returnt.resolve()
        for p in self.params:
            p.resolve()
        ParsedElement.NAMESPACE[self.qualname] = self

    def fix1(self):
        """see https://github.com/wlav/CPyCppyy/blob/master/src/Utility.cxx"""
        # TODO:
        #     gOpRemove.insert("new");
        #     gOpRemove.insert("new[]");
        #     gOpRemove.insert("delete");
        #     gOpRemove.insert("delete[]");

        renames = {
            "operator+": "__add__",
            "operator-": "__sub__",
            "operator*": "__mul__",
            "operator++": "__preinc__",
            "operator--": "__predec__",

            "operator[]": "__getitem__",
            "operator()": "__call__",
            "operator%": "__mod__",
            "operator**": "__pow__",
            "operator<<": "__lshift__",
            "operator>>": "__rshift__",
            "operator&": "__and__",
            "operator&&": "__dand__",
            "operator|": "__or__",
            "operator||": "__dor__",
            "operator^": "__xor__",
            "operator~": "__invert__",
            "operator,": "__comma__",
            "operator+=": "__iadd__",
            "operator-=": "__isub__",
            "operator*=": "__imul__",
            "operator/=": "__idiv__",
            "operator%=": "__imod__",
            "operator**=": "__ipow__",
            "operator<<=": "__ilshift__",
            "operator>>=": "__irshift__",
            "operator&=": "__iand__",
            "operator|=": "__ior__",
            "operator^=": "__ixor__",
            "operator==": "__eq__",
            "operator!=": "__ne__",
            "operator>": "__gt__",
            "operator<": "__lt__",
            "operator>=": "__ge__",
            "operator<=": "__le__",
            "operator->": "__follow__",
            "operator=": "__assign__",
        }
        if self.name == "operator+" and len(self.params) == 0:
            self.name = "__pos__"
        if self.name == "operator-" and len(self.params) == 0:
            self.name = "__neg__"
        if self.name == "operator*" and len(self.params) == 0:
            self.name = "__deref__"
        if self.name == "operator++" and len(self.params) == 1 and self.params[0].type == "int":
            self.name = "__postinc__"
        if self.name == "operator--" and len(self.params) == 1 and self.params[0].type == "int":
            self.name = "__postdec__"
        self.name = renames.get(self.name, self.name)

    def fix2(self):
        op, _, tp = self.name.partition(" ")
        if op == "operator" and tp:
            self.name = "__%s__" % simple_parse(tp)

    def fix3(self):
        if self.name.startswith("~"):
            self.name = "__destruct__"

    def fix4(self):
        self.name = "_" + self.name

    def fix5(self):
        self.name = re.sub("[^a-zA-Z]", "", self.name[1:])

    def __str__(self):
        res = []
        # for t in self.templ:
        #     res.append("{T} = TypeVar('{T}')".format(T=t[2].strip()))
        if self.overloaded:
            res.append("@overload")
        params = ", ".join(chain(["self"], (str(p) for p in self.params)))
        res.append("def {name}({params}) -> {returnt}:".format(name=self.name, params=params,
                                                               returnt=self.returnt or "None"))
        if self.brief:
            res.append('\t"""%s"""' % self.brief)
        res.append('\t...')
        return "\n".join(res)


class Variable(ParsedElement):
    def __init__(self, xml: Element, container: Optional[ParsedElement]):
        super().__init__(xml, container)
        self.name = xml.find("name").text
        self.type = Type(xml.find("type"), container)
        self.value = "..."

        self.check()

    def resolve(self):
        if hasattr(self.type, "resolve"):
            self.type.resolve()
        ParsedElement.NAMESPACE[self.qualname] = self

    def fix1(self):
        self.name = "_" + self.name

    def fix2(self):
        self.name = re.sub("[^a-zA-Z]", "", self.name[1:])

    def fix3(self):
        self.name = "_" + self.name

    def __str__(self):
        res = []
        if self.brief:
            res.append("#: " + self.brief)
        if self.type:
            res.append("{name} : {type} = {value}".format(name=self.name, type=self.type, value=self.value))
        else:
            res.append("{name} = {value}".format(name=self.name, value=self.value))
        return "\n".join(res)


class Enum(ParsedElement):
    def __init__(self, xml: Element, container: Optional[ParsedElement]):
        super().__init__(xml, container)
        self.qualname = type_parse(xml.find("compoundname"), container)
        self.name = xml.find("name").text.strip()
        self.strong = xml.attrib["strong"] == "yes"
        self.values = []
        for ev in xml.iterchildren("enumvalue"):
            v = Variable(ev, self)
            v.value = "enum.auto()"
            self.values.append(v)

        self.check()

    def resolve(self):
        ParsedElement.NAMESPACE[self.qualname] = self

    def __str__(self):
        res = ["class %s(%s):" % (self.name, "enum.Enum" if self.strong else "enum.IntEnum")]
        if self.brief:
            res.append('\t"""%s"""' % self.brief)
        for member in self.values:
            res.append(indent(str(member), "\t"))
        if not self.values:
            res.append("\t...")
        return "\n\n".join(res)


class Class(ParsedElement):
    def __init__(self, xml: Element, container: Optional[ParsedElement]):
        super().__init__(xml, container)
        self.qualname = type_parse(xml.find("compoundname"), container)
        if "[" in self.qualname:
            print("can't handle generic args to parent of", self.qualname, file=sys.stderr)
            self.qualname, _, generic_args = self.qualname.partition("[")
        self.name = self.qualname.split(".")[-1]
        self.bases = [Type(t, self) for t in xml.findall("basecompoundref")]
        self.kind = xml.attrib["kind"]
        self.templates = set()

        templ = xml.find("templateparamlist")
        if templ is not None:
            self.generic = [Template(param, container) for param in templ.iterfind("param")]
            self.templates.update(self.generic)
        else:
            self.generic = []

        self.members = list(self.do_iter(xml))
        self.namespace = defaultdict(list)
        self.subtypes = []
        for member in self.members:
            if hasattr(member, "templ"):
                self.templates.update(member.templ)
                if member.name == self.name:
                    member.name = "__init__"
            if hasattr(member, "name"):
                self.namespace[member.name].append(member)
            if isinstance(member, Type):
                self.subtypes.append(member)
            # TODO handle others?
        for name, members in self.namespace.items():
            if len(members) > 1:
                for member in members:
                    member.overloaded = True

        self.check()

    def resolve(self):
        for b in self.bases:
            b.resolve()
        for m in self.members:
            if hasattr(m, "resolve"):
                m.resolve()
        ParsedElement.NAMESPACE[self.qualname] = self

    def do_iter(self, root: Element):
        container = self if self.kind != "group" else self.container
        for member in root.iterchildren():
            if member.tag == "sectiondef":
                # TODO use these sections in the sphinx file?
                # {'kind': '(private|public)(-static)?-(func|attrib)|friend'} ['memberdef'+]
                # {'kind': 'user-defined'} ['header', 'description', 'memberdef'+]
                yield from self.do_iter(member)
            elif member.tag == "header":
                yield "# " + member.text
            elif member.tag == "memberdef":
                if member.attrib["prot"] == "private":
                    continue
                if member.attrib["kind"] == "variable":
                    yield Variable(member, container)
                elif member.attrib["kind"] == "function":
                    yield Function(member, container)
                elif member.attrib["kind"] == "typedef":
                    var = Variable(member, container)
                    var.value = var.type
                    var.type = "TypeAlias"
                    templ = member.find("templateparamlist")
                    if templ is not None:
                        self.templates.update(Template(param, container) for param in templ.iterfind("param"))
                    yield var
                elif member.attrib["kind"] == "enum":
                    yield Enum(member, container)
                elif member.attrib["kind"] != "friend":
                    print("can't handle", self.name, member.attrib["kind"], "member", member.attrib, file=sys.stderr)
                    UNKNOWN_MEMBER_DEFS.add(member.attrib["kind"])
            elif member.tag in ["location", "includes"]:
                # TODO handle?
                continue
            elif member.tag in ["innerclass", "innernamespace"]:
            elif member.tag not in ["compoundname", "briefdescription", "detaileddescription", "description", "collaborationgraph", "listofallmembers",
                yield Type(member, container)
                                    "derivedcompoundref", "basecompoundref", "inheritancegraph", "templateparamlist"]:
                print("can't handle", self.name, "field", member.tag, member.attrib, file=sys.stderr)
                UNKNOWN_OTHER_DEFS.add(member.tag)

    def __str__(self):
        res = []
        all = []
        for st in self.subtypes:
            if not st: continue
            stn = str(st).split("[")[0]
            res.append("from %s import *" % stn)
            all.append(stn.removeprefix(self.qualname + "."))
        if self.kind == "namespace":
            all.extend(self.namespace.keys())
        else:
            all.append(self.name)
        res.append("__all__ = %r" % all)

        ind = ""
        if self.kind != "namespace":
            res.extend(str(t) for t in self.templates)
            bases = [str(b) for b in self.bases]
            if self.generic:
                bases.append("Generic[%s]" % ", ".join(g.name for g in self.generic))
            if bases:
                bases = ", ".join(bases)
            else:
                bases = "object"
            res.append("class %s(%s):" % (self.name, bases))
            ind = "\t"

        if self.brief:
            res.append(indent('"""%s"""' % self.brief, ind))
        for member in self.members:
            if isinstance(member, Type): continue
            res.append(indent(str(member), ind))
        if not self.members:
            res.append(ind + "...")
        return "\n\n".join(res)


class Type(ParsedElement):
    LIST = []

    def __init__(self, xml: Element, container: Optional[ParsedElement], register=True):
        self.override = ""
        self.parts = []
        self.target = None

        if xml is None:
            self.xml = self.container = self.brief = None
            return

        if register:
            Type.LIST.append(self)
        super().__init__(xml, container)

        self.parts = [simple_parse(xml.text)]
        for t in xml.iterchildren():
            self.parts.append(Type(t, None))
            self.parts.append(simple_parse(t.tail))
        self.parts = [s for s in self.parts if s]
        if self.is_empty() == bool(self.parts):
            print("weird type", repr(self).strip(), file=sys.stderr)

        t = "".join(str(p) for p in self.parts)
        conditional = re.match(r"(std\.)?conditional *\[(.*),(?P<true>.*),(?P<false>.*)\]\.type", t)
        if conditional:
            self.override = "Union[%s, %s]" % (simple_parse(conditional.group("true")), simple_parse(conditional.group("false")))
        enable_if = re.match(r"(std\.)?enable_if *\[(.*),(?P<true>.*)\]\.type", t)
        if enable_if:
            self.override = 'Annotated[%s, "%s"]' % (enable_if.group("true"), t.replace('"', "'"))
        if t.startswith("std.function") or t.startswith("function"):
            self.override = "Callable"
        if "-" in t or "@" in t or t.endswith("."):
            self.override = 'WTF_TYPE["%s"]' % t.replace('"', "'")

    def is_empty(self):
        if self.xml is None:
            return True
        if self.xml.attrib:
            return False
        if len(self.xml) > 0:
            return False
        if self.xml.text:
            if self.xml.text.strip():
                return False
        return True

    def resolve(self):
        if self.is_empty(): return
        for p in self.parts:
            if hasattr(p, "resolve"):
                p.resolve()
        # if self.xml.tag == "ref":
        self.target = ParsedElement.INSTANCES.get(self.xml.attrib.get("refid", ""), None)
        if not self.target and self.parts:
            qn = getattr(self.parts[0], "qualname", None)
            if qn in ParsedElement.NAMESPACE:  # FIXME
                self.target = ParsedElement.NAMESPACE[qn]

    def is_resolvable(self):
        self.resolve()
        if self.target is not None:
            return True
        if not self.parts:
            return False
        if hasattr(self.parts[0], "is_resolvable"):
            return self.parts[0].is_resolvable()
        return False

    def __bool__(self):
        return bool(self.parts) or bool(self.override) or bool(self.target)

    def __str__(self):
        if self.override:
            return self.override
        if self.target:
            return self.target.qualname
        if self.parts:
            return "".join(str(p) for p in self.parts)
        return "Any"


def parse_default(xml):
    val = "".join(simple_parse(t) for t in xml.itertext())
    if "[[" in val and "]" not in val:
        val = val.replace("[[", "<<")
    return val


def simple_parse(orig_name):
    if not orig_name:
        return ""

    name = re.sub(r"\b(const +)?char +\*", "str", orig_name)
    name = name.replace("::", ".").replace("<", "[").replace(">", "]") \
        .replace("&", "").replace("*", "").replace("...", "").strip()  # TODO handle varargs
    name = re.sub(r"\bconst(expr)?\b", "", name)
    name = re.sub(r"\bvolatile\b", "", name)

    name = re.sub(r"\bvoid\b", "None", name)
    name = re.sub(r"\bu?int[0-9]+(_t)?\b", "int", name)
    name = re.sub(r"\bunsigned\b", "", name)
    name = re.sub(r"\b( *(int|short|long|byte|char))+\b", " int", name)
    name = re.sub(r"\bdouble\b", "float", name)
    name = re.sub(r"\b(std\.)?string\b", "str", name)
    name = re.sub(r"\b(std\.)?vector\b", "List", name)
    name = re.sub(r"\b(std\.)?pair\b", "Tuple", name)
    name = re.sub(r"\b(std\.)?(unordered_)?map\b", "Dict", name)

    name = re.sub(r"\btrue\b", "True", name)
    name = re.sub(r"\bfalse\b", "False", name)
    name = re.sub(r"\bnullptr\b", "None", name)
    name = re.sub(r"\bNULL\b", "None", name)

    name = re.sub(r"^class\b", "", name)
    name = re.sub(r"^typename\b", "", name)

    return name.strip()


def type_parse(orig_name, container=None):
    return str(Type(orig_name, container, register=False))


if __name__ == "__main__":
    from ogdf_python.doxygen import DOXYGEN_DATA, DOXYGEN_XML_DIR

    STUB_DIR = "stubs/ogdf_python/"

    compounds = []
    for l in ["class", "struct", "namespace"]:
        for clazz in DOXYGEN_DATA[l].values():
            if not clazz["name"].startswith("ogdf"): continue
            file_in = "%s/%s.xml" % (DOXYGEN_XML_DIR, clazz["refid"])
            compound_xml: Element = etree.parse(file_in)
            compounddef: Element = compound_xml.find("compounddef")
            if compounddef.attrib.get("prot", "") == "private": continue
            compounds.append(Class(compounddef))

    for parsed in compounds:
        parsed.resolve()
        if parsed.xml.find("innerclass") is not None:
            file_out = STUB_DIR + parsed.qualname.replace(".", "/") + "/__init__.pyi"
        else:
            file_out = STUB_DIR + parsed.qualname.replace(".", "/") + ".pyi"

        os.makedirs(os.path.dirname(file_out), exist_ok=True)
        with open(file_out, "wt") as f:
            print("# file %s generated from %s" % (file_out, parsed.xml.attrib["id"]), file=f)
            print("import enum", file=f)
            print("from typing import *", file=f)
            print("import ogdf_python.ogdf as ogdf", file=f)
            print(file=f)
            print(parsed, file=f)
        # sh.mypy(f)

    from pprint import pprint

    print("UNKNOWN_MEMBER_DEFS")
    pprint(UNKNOWN_MEMBER_DEFS)
    print("UNKNOWN_OTHER_DEFS")
    pprint(UNKNOWN_OTHER_DEFS)
    print("Type LIST")
    pprint({str(t): repr(t) for t in Type.LIST if not t.is_resolvable()}, width=300)

    sh.black("./stubs/")
