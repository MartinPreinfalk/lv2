LV2
===

LV2 is a plugin standard for audio systems.  It defines an extensible C API for
plugins, and a format for self-contained "bundle" directories that contain
plugins, metadata, and other resources.  See <http://lv2plug.in/> for more
information.

This package contains specifications (C headers and Turtle data files),
documentation generation tools, tests, and example plugins.

Installation
------------

See the [installation instructions](INSTALL.md) for details on how to
configure, build, and install LV2 with meson.

By default, everything is installed within the `prefix` with a UNIX-style
hierarchy, and LV2 bundles are installed in the "lv2" subdirectory of the
`libdir`.  The bundle installation directory can be overridden with the
`lv2dir` option.  For example, standard system-wide values for various
operating systems are:

    meson configure -Dlv2dir=/Library/Audio/Plug-Ins/LV2
    meson configure -Dlv2dir=/boot/common/add-ons/lv2
    meson configure -Dlv2dir=C:/Program Files/Common/LV2

The [specification bundles](lv2) are run-time dependencies of LV2 applications.
Programs expect their data to be available somewhere in `LV2_PATH`.  See
<http://lv2plug.in/pages/filesystem-hierarchy-standard.html> for details on the
standard installation paths.

Headers
-------

The `lv2/` include namespace is reserved for this LV2 distribution.
Other projects may extend LV2, but must place their headers elsewhere.

Headers are installed to `includedir` with paths like:

    #include "lv2/urid/urid.h"

For backwards compatibility, if the `old_headers` option is set, then headers
are also installed to the older URI-based paths:

    #include "lv2/lv2plug.in/ns/ext/urid/urid.h"

Projects still using this style are encourated to migrate to the shorter style
above.
