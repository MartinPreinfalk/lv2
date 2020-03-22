#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# lv2specgen, a documentation generator for LV2 specifications.
# Copyright (c) 2009-2014 David Robillard <d@drobilla.net>
#
# Based on SpecGen:
# <http://forge.morfeo-project.org/wiki_en/index.php/SpecGen>
# Copyright (c) 2003-2008 Christopher Schmidt <crschmidt@crschmidt.net>
# Copyright (c) 2005-2008 Uldis Bojars <uldis.bojars@deri.org>
# Copyright (c) 2007-2008 Sergio Fernández <sergio.fernandez@fundacionctic.org>
#
# This software is licensed under the terms of the MIT License.
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.

import datetime
import markdown
import markdown.extensions
import optparse
import os
import re
import sys
import time
import xml.sax.saxutils
import xml.dom
import xml.dom.minidom

__date__    = "2011-10-26"
__version__ = __date__.replace('-', '.')
__authors__ = """
Christopher Schmidt,
Uldis Bojars,
Sergio Fernández,
David Robillard"""
__license__ = "MIT License <http://www.opensource.org/licenses/mit>"
__contact__ = "devel@lists.lv2plug.in"

try:
    from lxml import etree
    have_lxml = True
except:
    have_lxml = False

try:
    import pygments
    import pygments.lexers
    import pygments.lexers.rdf
    import pygments.formatters
    have_pygments = True
except ImportError:
    print("Error importing pygments, syntax highlighting disabled")
    have_pygments = False

try:
    import rdflib
except ImportError:
    sys.exit("Error importing rdflib")

# Global Variables
classranges = {}
classdomains = {}
linkmap = {}
spec_url = None
spec_ns_str = None
spec_ns = None
spec_pre = None
spec_bundle = None
specgendir = None
ns_list = {
    "http://www.w3.org/1999/02/22-rdf-syntax-ns#"   : "rdf",
    "http://www.w3.org/2000/01/rdf-schema#"         : "rdfs",
    "http://www.w3.org/2002/07/owl#"                : "owl",
    "http://www.w3.org/2001/XMLSchema#"             : "xsd",
    "http://rdfs.org/sioc/ns#"                      : "sioc",
    "http://xmlns.com/foaf/0.1/"                    : "foaf",
    "http://purl.org/dc/elements/1.1/"              : "dc",
    "http://purl.org/dc/terms/"                     : "dct",
    "http://purl.org/rss/1.0/modules/content/"      : "content",
    "http://www.w3.org/2003/01/geo/wgs84_pos#"      : "geo",
    "http://www.w3.org/2004/02/skos/core#"          : "skos",
    "http://lv2plug.in/ns/lv2core#"                 : "lv2",
    "http://usefulinc.com/ns/doap#"                 : "doap",
    "http://ontologi.es/doap-changeset#"            : "dcs"
}

rdf  = rdflib.Namespace('http://www.w3.org/1999/02/22-rdf-syntax-ns#')
rdfs = rdflib.Namespace('http://www.w3.org/2000/01/rdf-schema#')
owl  = rdflib.Namespace('http://www.w3.org/2002/07/owl#')
lv2  = rdflib.Namespace('http://lv2plug.in/ns/lv2core#')
doap = rdflib.Namespace('http://usefulinc.com/ns/doap#')
dcs  = rdflib.Namespace('http://ontologi.es/doap-changeset#')
foaf = rdflib.Namespace('http://xmlns.com/foaf/0.1/')


def findStatements(model, s, p, o):
    return model.triples([s, p, o])


def findOne(m, s, p, o):
    l = findStatements(m, s, p, o)
    try:
        return sorted(l)[0]
    except:
        return None


def getSubject(s):
    return s[0]


def getPredicate(s):
    return s[1]


def getObject(s):
    return s[2]


def getLiteralString(s):
    return s


def isResource(n):
    return type(n) == rdflib.URIRef


def isBlank(n):
    return type(n) == rdflib.BNode


def isLiteral(n):
    return type(n) == rdflib.Literal


def niceName(uri):
    global spec_bundle
    if uri.startswith(spec_ns_str):
        return uri[len(spec_ns_str):]
    elif uri == str(rdfs.seeAlso):
        return "See also"

    regexp = re.compile("^(.*[/#])([^/#]+)$")
    rez = regexp.search(uri)
    if not rez:
        return uri
    pref = rez.group(1)
    if pref in ns_list:
        return ns_list.get(pref, pref) + ":" + rez.group(2)
    else:
        print("warning: prefix %s not in ns list:" % pref)
        print(ns_list)
        return uri


def termName(m, urinode):
    "Trims the namespace out of a term to give a name to the term."
    return str(urinode).replace(spec_ns_str, "")


def getLabel(m, urinode):
    l = findOne(m, urinode, rdfs.label, None)
    if l:
        return getLiteralString(getObject(l))
    else:
        return ''


def linkifyCodeIdentifiers(string):
    "Add links to code documentation for identifiers like LV2_Type"

    if linkmap == {}:
        return string

    if string in linkmap.keys():
        # Exact match for complete string
        return linkmap[string]

    rgx = re.compile('([^a-zA-Z0-9_:])(' + \
                     '|'.join(map(re.escape, linkmap)) + \
                     ')([^a-zA-Z0-9_:])')

    def translateCodeLink(match):
        return match.group(1) + linkmap[match.group(2)] + match.group(3)

    return rgx.sub(translateCodeLink, string)


def linkifyVocabIdentifiers(m, string, classlist, proplist, instalist):
    "Add links to vocabulary documentation for prefixed names like eg:Thing"

    rgx = re.compile('([a-zA-Z0-9_-]+):([a-zA-Z0-9_-]+)')
    namespaces = getNamespaces(m)

    def translateLink(match):
        text   = match.group(0)
        prefix = match.group(1)
        name   = match.group(2)
        curie = match.group(0)
        uri   = rdflib.URIRef(spec_ns + name)
        if prefix == spec_pre:
            if not ((classlist and uri in classlist) or
                    (instalist and uri in instalist) or
                    (proplist and uri in proplist)):
                print("warning: Link to undefined resource <%s>\n" % text)
            return '<a href="#%s">%s</a>' % (name, name)
        elif prefix in namespaces:
            return '<a href="%s">%s</a>' % (
                namespaces[match.group(1)] + match.group(2),
                match.group(0))
        else:
            return text

    return rgx.sub(translateLink, string)

def prettifyHtml(m, markup, subject, classlist, proplist, instalist):
    # Syntax highlight all C code
    if have_pygments:
        code_rgx = re.compile('<pre class="c-code">(.*?)</pre>', re.DOTALL)
        while True:
            code = code_rgx.search(markup)
            if not code:
                break
            match_str = xml.sax.saxutils.unescape(code.group(1))
            code_str = pygments.highlight(
                match_str,
                pygments.lexers.CLexer(),
                pygments.formatters.HtmlFormatter())
            markup = code_rgx.sub(code_str, markup, 1)

    # Syntax highlight all Turtle code
    if have_pygments:
        code_rgx = re.compile('<pre class="turtle-code">(.*?)</pre>', re.DOTALL)
        while True:
            code = code_rgx.search(markup)
            if not code:
                break
            match_str = xml.sax.saxutils.unescape(code.group(1))
            code_str = pygments.highlight(
                match_str,
                pygments.lexers.rdf.TurtleLexer(),
                pygments.formatters.HtmlFormatter())
            markup = code_rgx.sub(code_str, markup, 1)

    # Add links to code documentation for identifiers
    markup = linkifyCodeIdentifiers(markup)

    # Add internal links for known prefixed names
    markup = linkifyVocabIdentifiers(m, markup, classlist, proplist, instalist)

    # Transform names like #foo into links into this spec if possible
    rgx = re.compile('([ \t\n\r\f\v^]+)\#([a-zA-Z0-9_-]+)')
    def translateLocalLink(match):
        text  = match.group(0)
        space = match.group(1)
        name  = match.group(2)
        uri   = rdflib.URIRef(spec_ns + name)
        if ((classlist and uri in classlist) or
            (instalist and uri in instalist) or
            (proplist and uri in proplist)):
            return '%s<a href="#%s">%s</a>' % (space, name, name)
        else:
            print("warning: Link to undefined resource <%s>\n" % name)
            return text
    markup = rgx.sub(translateLocalLink, markup)

    if not have_lxml:
        print("warning: No Python lxml module found, output may be invalid")
    else:
        try:
            # Parse and validate documentation as XHTML Basic 1.1
            doc = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML Basic 1.1//EN"
                      "DTD/xhtml-basic11.dtd">
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="en">
  <head xml:lang="en" profile="profile">
    <title>Validation Skeleton Document</title>
  </head>
  <body>
""" + markup + """
  </body>
</html>"""

            oldcwd = os.getcwd()
            os.chdir(specgendir)
            parser = etree.XMLParser(dtd_validation=True, no_network=True)
            root = etree.fromstring(doc.encode('utf-8'), parser)
        except Exception as e:
            print("Invalid documentation for %s\n%s" % (subject, e))
            line_num = 1
            for line in doc.split('\n'):
                print('%3d: %s' % (line_num, line))
                line_num += 1
        finally:
            os.chdir(oldcwd)

    return markup


def formatDoc(m, urinode, literal, classlist, proplist, instalist):
    string = getLiteralString(literal)

    if literal.datatype == lv2.Markdown:
        ext = ["markdown.extensions.codehilite",
               "markdown.extensions.tables",
               "markdown.extensions.def_list"]

        doc = markdown.markdown(string, extensions=ext)

        # Hack to make tables valid XHTML Basic 1.1
        for tag in ['thead', 'tbody']:
            doc = doc.replace('<%s>\n' % tag, '')
            doc = doc.replace('</%s>\n' % tag, '')

        return prettifyHtml(m, doc, urinode, classlist, proplist, instalist)
    else:
        doc = xml.sax.saxutils.escape(string)
        doc = linkifyCodeIdentifiers(doc)
        doc = linkifyVocabIdentifiers(m, doc, classlist, proplist, instalist)
        return '<p>%s</p>' % doc


def getComment(m, subject, classlist, proplist, instalist):
    c = findOne(m, subject, rdfs.comment, None)
    if c:
        comment = getObject(c)
        return formatDoc(m, subject, comment, classlist, proplist, instalist)

    return ''


def getDetailedDocumentation(m, subject, classlist, proplist, instalist):
    markup = ''

    d = findOne(m, subject, lv2.documentation, None)
    if d:
        doc = getObject(d)
        if doc.datatype == lv2.Markdown:
            markup += formatDoc(m, subject, doc, classlist, proplist, instalist)
        else:
            html = getLiteralString(doc)
            markup += prettifyHtml(m, html, subject, classlist, proplist, instalist)

    return markup


def getFullDocumentation(m, subject, classlist, proplist, instalist):
    # Use rdfs:comment for first summary line
    markup = getComment(m, subject, classlist, proplist, instalist)

    # Use lv2:documentation for further details
    markup += getDetailedDocumentation(m, subject, classlist, proplist, instalist)

    return markup


def getProperty(val, first=True):
    "Return a string representing a property value in a property table"
    doc = ''
    if not first:
        doc += '<tr><th></th>'  # Empty cell in header column
    doc += '<td>%s</td></tr>\n' % val
    return doc


def endProperties(first):
    if first:
        return '</tr>'
    else:
        return ''


def rdfsPropertyInfo(term, m):
    """Generate HTML for properties: Domain, range"""
    global classranges
    global classdomains
    doc = ""
    range = ""
    domain = ""

    label = getLabel(m, term)
    if label != '':
        doc += "<tr><th>Label</th><td>%s</td></tr>" % label

    # Find subPropertyOf information
    rlist = ''
    first = True
    for st in findStatements(m, term, rdfs.subPropertyOf, None):
        k = getTermLink(getObject(st), term, rdfs.subPropertyOf)
        rlist += getProperty(k, first)
        first = False
    if rlist != '':
        doc += '<tr><th>Sub-property of</th>' + rlist

    # Domain stuff
    domains = findStatements(m, term, rdfs.domain, None)
    domainsdoc = ""
    first = True
    for d in sorted(domains):
        union = findOne(m, getObject(d), owl.unionOf, None)
        if union:
            uris = parseCollection(m, getObject(union))
            for uri in uris:
                domainsdoc += getProperty(getTermLink(uri, term, rdfs.domain), first)
                add(classdomains, uri, term)
        else:
            if not isBlank(getObject(d)):
                domainsdoc += getProperty(getTermLink(getObject(d), term, rdfs.domain), first)
        first = False
    if (len(domainsdoc) > 0):
        doc += "<tr><th>Domain</th>%s" % domainsdoc

    # Range stuff
    ranges = findStatements(m, term, rdfs.range, None)
    rangesdoc = ""
    first = True
    for r in sorted(ranges):
        union = findOne(m, getObject(r), owl.unionOf, None)
        if union:
            uris = parseCollection(m, getObject(union))
            for uri in uris:
                rangesdoc += getProperty(getTermLink(uri, term, rdfs.range), first)
                add(classranges, uri, term)
                first = False
        else:
            if not isBlank(getObject(r)):
                rangesdoc += getProperty(getTermLink(getObject(r), term, rdfs.range), first)
        first = False
    if (len(rangesdoc) > 0):
        doc += "<tr><th>Range</th>%s" % rangesdoc

    return doc


def parseCollection(model, node):
    uris = []

    while node:
        first = findOne(model, node, rdf.first, None)
        rest  = findOne(model, node, rdf.rest, None)
        if not first or not rest:
            break;

        uris.append(getObject(first))
        node = getObject(rest)

    return uris


def getTermLink(uri, subject=None, predicate=None):
    uri = str(uri)
    extra = ''
    if subject is not None and predicate is not None:
        extra = 'about="%s" rel="%s" resource="%s"' % (str(subject), niceName(str(predicate)), uri)
    if (uri.startswith(spec_ns_str)):
        return '<a href="#%s" %s>%s</a>' % (uri.replace(spec_ns_str, ""), extra, niceName(uri))
    else:
        return '<a href="%s" %s>%s</a>' % (uri, extra, niceName(uri))


def owlRestrictionInfo(term, m):
    """Generate OWL restriction information for Classes"""
    restrictions = []
    for s in findStatements(m, term, rdfs.subClassOf, None):
        if findOne(m, getObject(s), rdf.type, owl.Restriction):
            restrictions.append(getObject(s))

    if not restrictions:
        return ''

    doc = '<dl>'

    for r in sorted(restrictions):
        props = findStatements(m, r, None, None)
        onProp = None
        comment = None
        for p in props:
            if getPredicate(p) == owl.onProperty:
                onProp = getObject(p)
            elif getPredicate(p) == rdfs.comment:
                comment = getObject(p)
        if onProp is not None:
            doc += '<dt>Restriction on %s</dt>\n' % getTermLink(onProp)

            prop_str = ''
            for p in findStatements(m, r, None, None):
                if (getPredicate(p) == owl.onProperty
                    or getPredicate(p) == rdfs.comment
                    or (getPredicate(p) == rdf.type and getObject(p) == owl.Restriction)
                    or getPredicate(p) == lv2.documentation):
                    continue

                prop_str += getTermLink(getPredicate(p))

                if isResource(getObject(p)):
                    prop_str += ' ' + getTermLink(getObject(p))
                elif isLiteral(getObject(p)):
                    prop_str += ' ' + getLiteralString(getObject(p))

            if comment is not None:
                prop_str += '\n<div>%s</div>\n' % getLiteralString(comment)

            doc += '<dd>%s</dd>' % prop_str if prop_str else '';

    doc += '</dl>'
    return doc

def rdfsClassInfo(term, m):
    """Generate rdfs-type information for Classes: ranges, and domains."""
    global classranges
    global classdomains
    doc = ""

    label = getLabel(m, term)
    if label != '':
        doc += "<tr><th>Label</th><td>%s</td></tr>" % label

    # Find superclasses
    superclasses = set()
    for st in findStatements(m, term, rdfs.subClassOf, None):
        if not isBlank(getObject(st)):
            uri = getObject(st)
            superclasses |= set([uri])

    if len(superclasses) > 0:
        doc += "\n<tr><th>Subclass of</th>"
        first = True
        for superclass in sorted(superclasses):
            doc += getProperty(getTermLink(superclass), first)
            first = False

    # Find subclasses
    subclasses = set()
    for st in findStatements(m, None, rdfs.subClassOf, term):
        if not isBlank(getObject(st)):
            uri = getSubject(st)
            subclasses |= set([uri])

    if len(subclasses) > 0:
        doc += "\n<tr><th>Superclass of</th>"
        first = True
        for superclass in sorted(subclasses):
            doc += getProperty(getTermLink(superclass), first)
            first = False

    # Find out about properties which have rdfs:domain of t
    d = classdomains.get(str(term), "")
    if d:
        dlist = ''
        first = True
        for k in sorted(d):
            dlist += getProperty(getTermLink(k), first)
            first = False
        doc += "<tr><th>In domain of</th>%s" % dlist

    # Find out about properties which have rdfs:range of t
    r = classranges.get(str(term), "")
    if r:
        rlist = ''
        first = True
        for k in sorted(r):
            rlist += getProperty(getTermLink(k), first)
            first = False
        doc += "<tr><th>In range of</th>%s" % rlist

    return doc


def isSpecial(pred):
    """Return True if the predicate is "special" and shouldn't be emitted generically"""
    return pred in [rdf.type, rdfs.range, rdfs.domain, rdfs.label, rdfs.comment, rdfs.subClassOf, rdfs.subPropertyOf, lv2.documentation, owl.withRestrictions]


def blankNodeDesc(node, m):
    properties = findStatements(m, node, None, None)
    doc = ''
    last_pred = ''
    for p in sorted(properties):
        if isSpecial(getPredicate(p)):
            continue
        doc += '<tr>'
        doc += '<td class="blankterm">%s</td>\n' % getTermLink(getPredicate(p))
        if isResource(getObject(p)):
            doc += '<td class="blankdef">%s</td>\n' % getTermLink(getObject(p))
            # getTermLink(str(getObject(p)), node, getPredicate(p))
        elif isLiteral(getObject(p)):
            doc += '<td class="blankdef">%s</td>\n' % getLiteralString(getObject(p))
        elif isBlank(getObject(p)):
            doc += '<td class="blankdef">' + blankNodeDesc(getObject(p), m) + '</td>\n'
        else:
            doc += '<td class="blankdef">?</td>\n'
        doc += '</tr>'
    if doc != '':
        doc = '<table class="blankdesc">\n%s\n</table>\n' % doc
    return doc


def extraInfo(term, m):
    """Generate information about misc. properties of a term"""
    doc = ""
    properties = findStatements(m, term, None, None)
    first = True
    for p in sorted(properties):
        if isSpecial(getPredicate(p)):
            continue
        doc += '<tr><th>%s</th>\n' % getTermLink(getPredicate(p))
        if isResource(getObject(p)):
            doc += getProperty(getTermLink(getObject(p), term, getPredicate(p)), first)
        elif isLiteral(getObject(p)):
            doc += getProperty(linkifyCodeIdentifiers(str(getObject(p))), first)
        elif isBlank(getObject(p)):
            doc += getProperty(str(blankNodeDesc(getObject(p), m)), first)
        else:
            doc += getProperty('?', first)

    #doc += endProperties(first)

    return doc


def rdfsInstanceInfo(term, m):
    """Generate rdfs-type information for instances"""
    doc = ""

    label = getLabel(m, term)
    if label != '':
        doc += "<tr><th>Label</th><td>%s</td></tr>" % label

    first = True
    types = ""
    for match in sorted(findStatements(m, term, rdf.type, None)):
        types += getProperty(getTermLink(getObject(match),
                                         term,
                                         rdf.type),
                             first)
        first = False

    if types != "":
        doc += "<tr><th>Type</th>" + types

    doc += endProperties(first)

    return doc


def owlInfo(term, m):
    """Returns an extra information that is defined about a term using OWL."""
    res = ''

    # Inverse properties ( owl:inverseOf )
    first = True
    for st in findStatements(m, term, owl.inverseOf, None):
        res += getProperty(getTermLink(getObject(st)), first)
        first = False
    if res != "":
        res += endProperties(first)
        res = "<tr><th>Inverse:</th>\n" + res

    def owlTypeInfo(term, propertyType, name):
        if findOne(m, term, rdf.type, propertyType):
            return "<tr><th>Type</th><td>%s</td></tr>\n" % name
        else:
            return ""

    res += owlTypeInfo(term, owl.DatatypeProperty, "Datatype Property")
    res += owlTypeInfo(term, owl.ObjectProperty, "Object Property")
    res += owlTypeInfo(term, owl.AnnotationProperty, "Annotation Property")
    res += owlTypeInfo(term, owl.InverseFunctionalProperty, "Inverse Functional Property")
    res += owlTypeInfo(term, owl.SymmetricProperty, "Symmetric Property")

    return res

def isDeprecated(m, subject):
    deprecated = findOne(m, subject, owl.deprecated, None)
    return deprecated and (str(deprecated[2]).find("true") >= 0)

def docTerms(category, list, m, classlist, proplist, instalist):
    """
    A wrapper class for listing all the terms in a specific class (either
    Properties, or Classes. Category is 'Property' or 'Class', list is a
    list of term URI strings, return value is a chunk of HTML.
    """
    doc = ""
    nspre = spec_pre
    for term in list:
        if not term.startswith(spec_ns_str):
            sys.stderr.write("warning: Skipping external term `%s'" % term)
            continue

        t = termName(m, term)
        curie = term.split(spec_ns_str[-1])[1]
        doc += '<div class="specterm" id="%s" about="%s">' % (t, term)
        doc += '<h4><a href="#%s">%s</a></h4>' % (getAnchor(term), curie)

        comment = getFullDocumentation(m, term, classlist, proplist, instalist)
        is_deprecated = isDeprecated(m, term)

        doc += '<div class="spectermbody">'

        terminfo = ""
        extrainfo = ""
        if category == 'Property':
            terminfo += rdfsPropertyInfo(term, m)
            terminfo += owlInfo(term, m)
        if category == 'Class':
            terminfo += rdfsClassInfo(term, m)
            extrainfo += owlRestrictionInfo(term, m)
        if category == 'Instance':
            terminfo += rdfsInstanceInfo(term, m)

        terminfo += extraInfo(term, m)

        if (len(terminfo) > 0):  # to prevent empty list (bug #882)
            doc += '\n<table class="terminfo">%s</table>\n' % terminfo

        doc += '<div class="description">'

        if is_deprecated:
            doc += '<div class="warning">Deprecated</div>'

        if comment != '':
            doc += "<div class=\"comment\" property=\"rdfs:comment\">%s</div>" % comment

        doc += extrainfo

        doc += "</div>"

        doc += '</div>'
        doc += "\n</div>\n\n"

    return doc


def getShortName(uri):
    uri = str(uri)
    if ("#" in uri):
        return uri.split("#")[-1]
    else:
        return uri.split("/")[-1]


def getAnchor(uri):
    uri = str(uri)
    if (uri.startswith(spec_ns_str)):
        return uri[len(spec_ns_str):].replace("/", "_")
    else:
        return getShortName(uri)


def buildIndex(m, classlist, proplist, instalist=None):
    if not (classlist or proplist or instalist):
        return ''

    head = ''
    body = ''

    def termLink(m, t):
        if str(t).startswith(spec_ns_str):
            name = termName(m, t)
            return '<a href="#%s">%s</a>' % (name, name)
        else:
            return '<a href="%s">%s</a>' % (str(t), str(t))

    if (len(classlist) > 0):
        head += '<th><a href="#ref-classes" />Classes</th>'
        body += '<td><ul>'
        shown = {}
        for c in sorted(classlist):
            if c in shown:
                continue

            # Skip classes that are subclasses of classes defined in this spec
            local_subclass = False
            for p in findStatements(m, c, rdfs.subClassOf, None):
                parent = str(p[2])
                if parent[0:len(spec_ns_str)] == spec_ns_str:
                    local_subclass = True
            if local_subclass:
                continue

            shown[c] = True
            body += '<li>' + termLink(m, c)
            def class_tree(c):
                tree = ''
                shown[c] = True

                subclasses = []
                for s in findStatements(m, None, rdfs.subClassOf, c):
                    subclasses += [getSubject(s)]

                for s in sorted(subclasses):
                    tree += '<li>' + termLink(m, s)
                    tree += class_tree(s)
                    tree += '</li>'
                if tree != '':
                    tree = '<ul>' + tree + '</ul>'
                return tree
            body += class_tree(c)
            body += '</li>'
        body += '</ul></td>\n'

    if (len(proplist) > 0):
        head += '<th><a href="#ref-properties" />Properties</th>'
        body += '<td><ul>'
        for p in sorted(proplist):
            body += '<li>%s</li>' % termLink(m, p)
        body += '</ul></td>\n'

    if (instalist is not None and len(instalist) > 0):
        head += '<th><a href="#ref-instances" />Instances</th>'
        body += '<td><ul>'
        for i in sorted(instalist):
            p = getShortName(i)
            anchor = getAnchor(i)
            body += '<li><a href="#%s">%s</a></li>' % (anchor, p)
        body += '</ul></td>\n'

    if head and body:
        return '''<table class="index">
<thead><tr>%s</tr></thead>
<tbody><tr>%s</tr></tbody></table>
''' % (head, body)

    return ''

def add(where, key, value):
    if not key in where:
        where[key] = []
    if not value in where[key]:
        where[key].append(value)


def specInformation(m, ns):
    """
    Read through the spec (provided as a Redland model) and return classlist
    and proplist. Global variables classranges and classdomains are also filled
    as appropriate.
    """
    global classranges
    global classdomains

    # Find the class information: Ranges, domains, and list of all names.
    classtypes = [rdfs.Class, owl.Class, rdfs.Datatype]
    classlist = []
    for onetype in classtypes:
        for classStatement in findStatements(m, None, rdf.type, onetype):
            for range in findStatements(m, None, rdfs.range, getSubject(classStatement)):
                if not isBlank(getSubject(classStatement)):
                    add(classranges,
                        str(getSubject(classStatement)),
                        str(getSubject(range)))
            for domain in findStatements(m, None, rdfs.domain, getSubject(classStatement)):
                if not isBlank(getSubject(classStatement)):
                    add(classdomains,
                        str(getSubject(classStatement)),
                        str(getSubject(domain)))
            if not isBlank(getSubject(classStatement)):
                klass = getSubject(classStatement)
                if klass not in classlist and str(klass).startswith(ns):
                    classlist.append(klass)

    # Create a list of properties in the schema.
    proptypes = [rdf.Property, owl.ObjectProperty, owl.DatatypeProperty, owl.AnnotationProperty]
    proplist = []
    for onetype in proptypes:
        for propertyStatement in findStatements(m, None, rdf.type, onetype):
            prop = getSubject(propertyStatement)
            if prop not in proplist and str(prop).startswith(ns):
                proplist.append(prop)

    return classlist, proplist


def specProperty(m, subject, predicate):
    "Return a property of the spec."
    for c in findStatements(m, subject, predicate, None):
        return getLiteralString(getObject(c))
    return ''


def specProperties(m, subject, predicate):
    "Return a property of the spec."
    properties = []
    for c in findStatements(m, subject, predicate, None):
        properties += [getObject(c)]
    return properties


def specAuthors(m, subject):
    "Return an HTML description of the authors of the spec."

    subjects = [subject];
    p = findOne(m, subject, lv2.project, None)
    if p:
        subjects += [getObject(p)]

    dev = set()
    for s in subjects:
        for i in findStatements(m, s, doap.developer, None):
            for j in findStatements(m, getObject(i), foaf.name, None):
                dev.add(getLiteralString(getObject(j)))

    maint = set()
    for s in subjects:
        for i in findStatements(m, s, doap.maintainer, None):
            for j in findStatements(m, getObject(i), foaf.name, None):
                maint.add(getLiteralString(getObject(j)))

    doc = ''

    devdoc = ''
    first = True
    for d in sorted(dev):
        if not first:
            devdoc += ', '
        devdoc += '<span class="author" property="doap:developer">%s</span>' % d
        first = False
    if len(dev) == 1:
        doc += '<tr><th class="metahead">Developer</th><td>%s</td></tr>' % devdoc
    elif len(dev) > 0:
        doc += '<tr><th class="metahead">Developers</th><td>%s</td></tr>' % devdoc

    maintdoc = ''
    first = True
    for m in sorted(maint):
        if not first:
            maintdoc += ', '
        maintdoc += '<span class="author" property="doap:maintainer">%s</span>' % m
        first = False
    if len(maint) == 1:
        doc += '<tr><th class="metahead">Maintainer</th><td>%s</td></tr>' % maintdoc
    elif len(maint) > 0:
        doc += '<tr><th class="metahead">Maintainers</th><td>%s</td></tr>' % maintdoc

    return doc


def releaseChangeset(m, release, prefix=''):
    changeset = findOne(m, release, dcs.changeset, None)
    if changeset is None:
        return ''

    entry = ''
    #entry = '<dd><ul>\n'
    for i in sorted(findStatements(m, getObject(changeset), dcs.item, None)):
        item  = getObject(i)
        label = findOne(m, item, rdfs.label, None)
        if not label:
            print("error: dcs:item has no rdfs:label")
            continue

        text = getLiteralString(getObject(label))
        if prefix:
            text = prefix + ': ' + text

        entry += '<li>%s</li>\n' % text

    #entry += '</ul></dd>\n'
    return entry


def specHistoryEntries(m, subject, entries):
    for r in findStatements(m, subject, doap.release, None):
        release = getObject(r)
        revNode = findOne(m, release, doap.revision, None)
        if not revNode:
            print("error: doap:release has no doap:revision")
            continue

        rev = getLiteralString(getObject(revNode))

        created = findOne(m, release, doap.created, None)

        dist = findOne(m, release, doap['file-release'], None)
        if dist:
            entry = '<dt><a href="%s">Version %s</a>' % (getObject(dist), rev)
        else:
            entry = '<dt>Version %s' % rev
            #print("warning: doap:release has no doap:file-release")

        if created:
            entry += ' (%s)</dt>\n' % getLiteralString(getObject(created))
        else:
            entry += ' (<span class="warning">EXPERIMENTAL</span>)</dt>'

        entry += '<dd><ul>\n%s' % releaseChangeset(m, release)

        if dist is not None:
            entries[(getObject(created), getObject(dist))] = entry

    return entries


def specHistoryMarkup(entries):
    if len(entries) > 0:
        history = '<dl>\n'
        for e in sorted(entries.keys(), reverse=True):
            history += entries[e] + '</ul></dd>'
        history += '</dl>\n'
        return history
    else:
        return ''


def specHistory(m, subject):
    return specHistoryMarkup(specHistoryEntries(m, subject, {}))


def specVersion(m, subject):
    """
    Return a (minorVersion, microVersion, date) tuple
    """
    # Get the date from the latest doap release
    latest_doap_revision = ""
    latest_doap_release = None
    for i in findStatements(m, subject, doap.release, None):
        for j in findStatements(m, getObject(i), doap.revision, None):
            revision = getLiteralString(getObject(j))
            if latest_doap_revision == "" or revision > latest_doap_revision:
                latest_doap_revision = revision
                latest_doap_release = getObject(i)
    date = ""
    if latest_doap_release is not None:
        for i in findStatements(m, latest_doap_release, doap.created, None):
            date = getLiteralString(getObject(i))

    # Get the LV2 version
    minor_version = 0
    micro_version = 0
    for i in findStatements(m, None, lv2.minorVersion, None):
        minor_version = int(getLiteralString(getObject(i)))
    for i in findStatements(m, None, lv2.microVersion, None):
        micro_version = int(getLiteralString(getObject(i)))
    return (minor_version, micro_version, date)


def getInstances(model, classes, properties):
    """
    Extract all resources instanced in the ontology
    (aka "everything that is not a class or a property")
    """
    instances = []
    for c in classes:
        for i in findStatements(model, None, rdf.type, c):
            if not isResource(getSubject(i)):
                continue
            inst = getSubject(i)
            if inst not in instances and str(inst) != spec_url:
                instances.append(inst)
    for i in findStatements(model, None, rdf.type, None):
        if ((not isResource(getSubject(i)))
            or (getSubject(i) in classes)
            or (getSubject(i) in instances)
            or (getSubject(i) in properties)):
            continue
        full_uri = str(getSubject(i))
        if (full_uri.startswith(spec_ns_str)):
            instances.append(getSubject(i))
    return instances

def load_tags(path, docdir):
    "Build a (symbol => URI) map from a Doxygen tag file."

    if not path or not docdir:
        return {}

    def getChildText(elt, tagname):
        "Return the content of the first child node with a certain tag name."
        for e in elt.childNodes:
            if e.nodeType == xml.dom.Node.ELEMENT_NODE and e.tagName == tagname:
                return e.firstChild.nodeValue
        return ''

    def linkTo(filename, anchor, sym):
        if anchor:
            return '<span><a href="%s/%s#%s">%s</a></span>' % (docdir, filename, anchor, sym)
        else:
            return '<span><a href="%s/%s">%s</a></span>' % (docdir, filename, sym)

    tagdoc  = xml.dom.minidom.parse(path)
    root    = tagdoc.documentElement
    linkmap = {}
    for cn in root.childNodes:
        if (cn.nodeType == xml.dom.Node.ELEMENT_NODE
            and cn.tagName == 'compound'
            and cn.getAttribute('kind') != 'page'):

            name     = getChildText(cn, 'name')
            filename = getChildText(cn, 'filename')
            anchor   = getChildText(cn, 'anchor')
            if not filename.endswith('.html'):
                filename += '.html'

            if cn.getAttribute('kind') != 'group':
                linkmap[name] = linkTo(filename, anchor, name)

            prefix = ''
            if cn.getAttribute('kind') == 'struct':
                prefix = name + '::'

            members = cn.getElementsByTagName('member')
            for m in members:
                mname   = prefix + getChildText(m, 'name')
                mafile  = getChildText(m, 'anchorfile')
                manchor = getChildText(m, 'anchor')
                linkmap[mname] = linkTo(mafile, manchor, mname)

    return linkmap


def writeIndex(model, specloc, index_path, root_path, root_uri):
    # Get extension URI
    ext_node = model.value(None, rdf.type, lv2.Specification)
    if not ext_node:
        ext_node = model.value(None, rdf.type, owl.Ontology)
    if not ext_node:
        print('no extension found in %s' % bundle)
        sys.exit(1)

    ext = str(ext_node)

    # Get version
    minor = 0
    micro = 0
    try:
        minor = int(model.value(ext_node, lv2.minorVersion, None))
        micro = int(model.value(ext_node, lv2.microVersion, None))
    except:
        e = sys.exc_info()[1]
        print('warning: %s: failed to find version for %s' % (bundle, ext))

    # Get date
    date = None
    for r in model.triples([ext_node, doap.release, None]):
        revision = model.value(r[2], doap.revision, None)
        if str(revision) == ('%d.%d' % (minor, micro)):
            date = model.value(r[2], doap.created, None)
            break

    # Verify that this date is the latest
    for r in model.triples([ext_node, doap.release, None]):
        this_date = model.value(r[2], doap.created, None)
        if this_date > date:
            print('warning: %s revision %d.%d (%s) is not the latest release' % (
                ext_node, minor, micro, date))
            break

    # Get name and short description
    name      = model.value(ext_node, doap.name, None)
    shortdesc = model.value(ext_node, doap.shortdesc, None)

    # Chop 'LV2' prefix from name for cleaner index
    if name.startswith('LV2 '):
        name = name[4:]

    # Find relative link target
    if root_uri and ext_node.startswith(root_uri):
        target = ext_node[len(root_uri):] + '.html'
    else:
        target = os.path.relpath(ext_node, root_path) + '.html'

    stem = os.path.splitext(os.path.basename(target))[0]

    # Specification (comment is to act as a sort key)
    row = '<tr><!-- %s --><td><a rel="rdfs:seeAlso" href="%s">%s</a></td>' % (
        b, target, name)

    # API
    row += '<td><a rel="rdfs:seeAlso" href="../doc/html/group__%s.html">%s</a></td>' % (
        stem, name)

    # Description
    if shortdesc:
        row += '<td>' + str(shortdesc) + '</td>'
    else:
        row += '<td></td>'

    # Version
    version_str = '%s.%s' % (minor, micro)
    if minor == 0 or (micro % 2 != 0):
        row += '<td><span style="color: red">' + version_str + '</span></td>'
    else:
        row += '<td>' + version_str + '</td>'

    # Status
    deprecated = model.value(ext_node, owl.deprecated, None)
    if minor == 0:
        row += '<td><span class="error">Experimental</span></td>'
    elif deprecated and str(deprecated[2]) != "false":
        row += '<td><span class="warning">Deprecated</span></td>'
    elif micro % 2 == 0:
        row += '<td><span class="success">Stable</span></td>'

    row += '</tr>'

    index = open(index_path, 'w')
    index.write(row)
    index.close()


def specgen(specloc, indir, style_uri, docdir, tags, opts, instances=False, root_link=None, index_path=None, root_path=None, root_uri=None):
    """The meat and potatoes: Everything starts here."""

    global spec_bundle
    global spec_url
    global spec_ns_str
    global spec_ns
    global spec_pre
    global ns_list
    global specgendir
    global linkmap

    spec_bundle = "file://%s/" % os.path.abspath(os.path.dirname(specloc))
    specgendir = os.path.abspath(indir)

    # Template
    temploc = os.path.join(indir, "template.html")
    template = None
    f = open(temploc, "r")
    template = f.read()
    f.close()

    # Load code documentation link map from tags file
    linkmap = load_tags(tags, docdir)

    m = rdflib.ConjunctiveGraph()
    manifest_path = os.path.join(os.path.dirname(specloc), 'manifest.ttl')
    if os.path.exists(manifest_path):
        m.parse(manifest_path, format='n3')
    m.parse(specloc, format='n3')

    bundle_path = os.path.split(specloc[specloc.find(':') + 1:])[0]
    abs_bundle_path = os.path.abspath(bundle_path)
    spec_url = getOntologyNS(m)
    spec = rdflib.URIRef(spec_url)

    # Load all seeAlso files recursively
    seeAlso = set()
    done = False
    while not done:
        done = True
        for uri in specProperties(m, spec, rdfs.seeAlso):
            if uri[:7] == 'file://':
                path = uri[7:]
                if (path != os.path.abspath(specloc) and
                    path.endswith('ttl') and
                    path not in seeAlso):
                    seeAlso.add(path)
                    m.parse(path, format='n3')
                    done = False

    spec_ns_str = spec_url
    if (spec_ns_str[-1] != "/" and spec_ns_str[-1] != "#"):
        spec_ns_str += "#"

    spec_ns = rdflib.Namespace(spec_ns_str)

    namespaces = getNamespaces(m)
    keys = sorted(namespaces.keys())
    prefixes_html = "<span>"
    for i in keys:
        uri = namespaces[i]
        if uri.startswith('file:'):
            continue;
        ns_list[str(uri)] = i
        if (str(uri) == spec_url + '#' or
            str(uri) == spec_url + '/' or
            str(uri) == spec_url):
            spec_pre = i
        prefixes_html += '<a href="%s">%s</a> ' % (uri, i)
    prefixes_html += "</span>"

    if spec_pre is None:
        print('No namespace prefix for %s defined' % specloc)
        sys.exit(1)

    ns_list[spec_ns_str] = spec_pre

    classlist, proplist = specInformation(m, spec_ns_str)
    classlist = sorted(classlist)
    proplist = sorted(proplist)

    instalist = None
    if instances:
        instalist = sorted(getInstances(m, classlist, proplist),
                           key=lambda x: getShortName(x).lower())

    azlist = buildIndex(m, classlist, proplist, instalist)

    # Generate Term HTML
    classlist = docTerms('Class', classlist, m, classlist, proplist, instalist)
    proplist = docTerms('Property', proplist, m, classlist, proplist, instalist)
    if instances:
        instlist = docTerms('Instance', instalist, m, classlist, proplist, instalist)

    termlist = ''
    if classlist:
        termlist += '<h3><a id="ref-classes" />Classes</h3>' + classlist
    if proplist:
        termlist += '<h3><a id="ref-properties" />Properties</h3>' + proplist
    if instlist:
        termlist += '<h3><a id="ref-instances" />Instances</h3>' + instlist

    name = specProperty(m, spec, doap.name)
    title = name
    if root_link:
        name = '<a href="%s">%s</a>' % (root_link, name)

    template = template.replace('@TITLE@', title)
    template = template.replace('@NAME@', name)
    template = template.replace('@SHORT_DESC@', specProperty(m, spec, doap.shortdesc))
    template = template.replace('@URI@', spec)
    template = template.replace('@PREFIX@', spec_pre)
    if spec_pre == 'lv2':
        template = template.replace('@XMLNS@', '')
    else:
        template = template.replace('@XMLNS@', '      xmlns:%s="%s"' % (spec_pre, spec_ns_str))

    filename = os.path.basename(specloc)
    basename = filename[0:filename.rfind('.')]

    template = template.replace('@STYLE_URI@', style_uri)
    template = template.replace('@PREFIXES@', str(prefixes_html))
    template = template.replace('@BASE@', spec_ns_str)
    template = template.replace('@AUTHORS@', specAuthors(m, spec))
    template = template.replace('@INDEX@', azlist)
    template = template.replace('@REFERENCE@', termlist)
    template = template.replace('@FILENAME@', filename)
    template = template.replace('@HEADER@', basename + '.h')
    template = template.replace('@HISTORY@', specHistory(m, spec))

    mail_row = ''
    if 'list_email' in opts:
        mail_row = '<tr><th>Discuss</th><td><a href="mailto:%s">%s</a>' % (
            opts['list_email'], opts['list_email'])
        if 'list_page' in opts:
            mail_row += ' <a href="%s">(subscribe)</a>' % opts['list_page']
        mail_row += '</td></tr>'
    template = template.replace('@MAIL@', mail_row)

    version = specVersion(m, spec)  # (minor, micro, date)
    date_string = version[2]
    if date_string == "":
        date_string = "Undated"

    version_string = "%s.%s" % (version[0], version[1])
    experimental = (version[0] == 0 or version[1] % 2 == 1)
    if experimental:
        version_string += ' <span class="warning">EXPERIMENTAL</span>'

    if isDeprecated(m, rdflib.URIRef(spec_url)):
        version_string += ' <span class="warning">DEPRECATED</span>'

    template = template.replace('@VERSION@', version_string)

    content_links = ''
    if docdir is not None:
        content_links = '<li><a href="%s">API</a></li>' % os.path.join(docdir, 'group__%s.html' % basename)

    template = template.replace('@CONTENT_LINKS@', content_links)

    docs = getDetailedDocumentation(m, rdflib.URIRef(spec_url), classlist, proplist, instalist)
    template = template.replace('@DESCRIPTION@', docs)

    now = int(os.environ.get('SOURCE_DATE_EPOCH', time.time()))
    build_date = datetime.datetime.utcfromtimestamp(now)
    template = template.replace('@DATE@', build_date.strftime('%F'))
    template = template.replace('@TIME@', build_date.strftime('%F %H:%M UTC'))

    # Write index row
    if index_path is not None:
        writeIndex(m, specloc, index_path, root_path, root_uri)

    # Validate complete output page
    try:
        oldcwd = os.getcwd()
        os.chdir(specgendir)
        root = etree.fromstring(
            template.replace('"http://www.w3.org/MarkUp/DTD/xhtml-rdfa-1.dtd"',
                             '"DTD/xhtml-rdfa-1.dtd"').encode('utf-8'),
            etree.XMLParser(dtd_validation=True, no_network=True))
    except Exception as e:
        sys.stderr.write("error: Validation failed for %s: %s" % (specloc, e))
    finally:
        os.chdir(oldcwd)

    return template


def save(path, text):
    try:
        f = open(path, "w")
        f.write(text)
        f.flush()
        f.close()
    except Exception:
        e = sys.exc_info()[1]
        print('Error writing to file "' + path + '": ' + str(e))


def getNamespaces(m):
    """Return a prefix:URI dictionary of all namespaces seen during parsing"""
    nspaces = {}
    for prefix, uri in m.namespaces():
        if not re.match('default[0-9]*', prefix) and not prefix == 'xml':
            # Skip silly default namespaces added by rdflib
            nspaces[prefix] = uri
    return nspaces


def getOntologyNS(m):
    ns = None
    s = findOne(m, None, rdf.type, lv2.Specification)
    if not s:
        s = findOne(m, None, rdf.type, owl.Ontology)
    if s:
        if not isBlank(getSubject(s)):
            ns = str(getSubject(s))

    if (ns == None):
        sys.exit("Impossible to get ontology's namespace")
    else:
        return ns


def usage():
    script = os.path.basename(sys.argv[0])
    return "Usage: %s ONTOLOGY_TTL OUTPUT_HTML [OPTION]..." % script

if __name__ == "__main__":
    """Ontology specification generator tool"""

    indir = os.path.abspath(os.path.dirname(sys.argv[0]))
    if not os.path.exists(os.path.join(indir, 'template.html')):
        indir = os.path.join(os.path.dirname(indir), 'share', 'lv2specgen')

    opt = optparse.OptionParser(usage=usage(),
                                description='Write HTML documentation for an RDF ontology.')
    opt.add_option('--list-email', type='string', dest='list_email',
                   help='Mailing list email address')
    opt.add_option('--list-page', type='string', dest='list_page',
                   help='Mailing list info page address')
    opt.add_option('--template-dir', type='string', dest='template_dir', default=indir,
                   help='Template directory')
    opt.add_option('--style-uri', type='string', dest='style_uri', default='style.css',
                   help='Stylesheet URI')
    opt.add_option('--docdir', type='string', dest='docdir', default=None,
                   help='Doxygen output directory')
    opt.add_option('--index', type='string', dest='index_path', default=None,
                   help='Index row output file')
    opt.add_option('--tags', type='string', dest='tags', default=None,
                   help='Doxygen tags file')
    opt.add_option('-r', '--root-path', type='string', dest='root_path', default='',
                   help='Root path')
    opt.add_option('-R', '--root-uri', type='string', dest='root_uri', default='',
                   help='Root URI')
    opt.add_option('-p', '--prefix', type='string', dest='prefix',
                   help='Specification Turtle prefix')
    opt.add_option('-i', '--instances', action='store_true', dest='instances',
                   help='Document instances')
    opt.add_option('--copy-style', action='store_true', dest='copy_style',
                   help='Copy style from template directory to output directory')

    (options, args) = opt.parse_args()
    opts = vars(options)

    if len(args) < 2:
        opt.print_help()
        sys.exit(-1)

    spec_pre   = options.prefix
    ontology   = "file:" + str(args[0])
    output     = args[1]
    index_path = options.index_path
    docdir     = options.docdir
    tags       = options.tags

    out    = '.'
    spec   = args[0]
    path   = os.path.dirname(spec)
    outdir = os.path.abspath(os.path.join(out, path))

    bundle = str(outdir)
    b = os.path.basename(outdir)

    if not os.access(os.path.abspath(spec), os.R_OK):
        print('warning: extension %s has no %s.ttl file' % (b, b))
        sys.exit(1)

    # Root link
    root_path = opts['root_path']
    root_uri  = opts['root_uri']
    root_link = os.path.join(root_path, 'index.html')

    # Generate spec documentation
    specdoc = specgen(
        spec,
        indir,
        opts['style_uri'],
        docdir,
        tags,
        opts,
        instances=True,
        root_link=root_link,
        index_path=index_path,
        root_path=root_path,
        root_uri=root_uri)

    # Save to HTML output file
    save(output, specdoc)

    if opts['copy_style']:
        import shutil
        shutil.copyfile(os.path.join(indir, 'style.css'),
                        os.path.join(os.path.dirname(output), 'style.css'))
