find_package(PkgConfig)

PKG_CHECK_MODULES(PC_GR_SAGE gnuradio-sage)

FIND_PATH(
    GR_SAGE_INCLUDE_DIRS
    NAMES gnuradio/sage/api.h
    HINTS $ENV{SAGE_DIR}/include
        ${PC_SAGE_INCLUDEDIR}
    PATHS ${CMAKE_INSTALL_PREFIX}/include
          /usr/local/include
          /usr/include
)

FIND_LIBRARY(
    GR_SAGE_LIBRARIES
    NAMES gnuradio-sage
    HINTS $ENV{SAGE_DIR}/lib
        ${PC_SAGE_LIBDIR}
    PATHS ${CMAKE_INSTALL_PREFIX}/lib
          ${CMAKE_INSTALL_PREFIX}/lib64
          /usr/local/lib
          /usr/local/lib64
          /usr/lib
          /usr/lib64
          )

include("${CMAKE_CURRENT_LIST_DIR}/gnuradio-sageTarget.cmake")

INCLUDE(FindPackageHandleStandardArgs)
FIND_PACKAGE_HANDLE_STANDARD_ARGS(GR_SAGE DEFAULT_MSG GR_SAGE_LIBRARIES GR_SAGE_INCLUDE_DIRS)
MARK_AS_ADVANCED(GR_SAGE_LIBRARIES GR_SAGE_INCLUDE_DIRS)
