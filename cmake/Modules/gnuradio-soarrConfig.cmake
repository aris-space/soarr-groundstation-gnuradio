find_package(PkgConfig)

PKG_CHECK_MODULES(PC_GR_SOARR gnuradio-soarr)

FIND_PATH(
    GR_SOARR_INCLUDE_DIRS
    NAMES gnuradio/soarr/api.h
    HINTS $ENV{SOARR_DIR}/include
        ${PC_SOARR_INCLUDEDIR}
    PATHS ${CMAKE_INSTALL_PREFIX}/include
          /usr/local/include
          /usr/include
)

FIND_LIBRARY(
    GR_SOARR_LIBRARIES
    NAMES gnuradio-soarr
    HINTS $ENV{SOARR_DIR}/lib
        ${PC_SOARR_LIBDIR}
    PATHS ${CMAKE_INSTALL_PREFIX}/lib
          ${CMAKE_INSTALL_PREFIX}/lib64
          /usr/local/lib
          /usr/local/lib64
          /usr/lib
          /usr/lib64
          )

include("${CMAKE_CURRENT_LIST_DIR}/gnuradio-soarrTarget.cmake")

INCLUDE(FindPackageHandleStandardArgs)
FIND_PACKAGE_HANDLE_STANDARD_ARGS(GR_SOARR DEFAULT_MSG GR_SOARR_LIBRARIES GR_SOARR_INCLUDE_DIRS)
MARK_AS_ADVANCED(GR_SOARR_LIBRARIES GR_SOARR_INCLUDE_DIRS)
