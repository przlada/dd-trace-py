# Only proceed if Datadog::Profiling (provided by libdatadog) isn't already defined
if(TARGET Datadog::Profiling)
    return()
endif()

# Set the FetchContent paths early
set(FETCHCONTENT_BASE_DIR
    "${CMAKE_CURRENT_BINARY_DIR}/_deps"
    CACHE PATH "FetchContent base directory")
set(FETCHCONTENT_DOWNLOADS_DIR
    "${FETCHCONTENT_BASE_DIR}/downloads"
    CACHE PATH "FetchContent downloads directory")

include_guard(GLOBAL)
include(FetchContent)

# Set version if not already set
if(NOT DEFINED TAG_LIBDATADOG)
    set(TAG_LIBDATADOG
        "v16.0.3"
        CACHE STRING "libdatadog github tag")
endif()

if(NOT DEFINED DD_CHECKSUMS)
    set(DD_CHECKSUMS
        "dd08d3a4dbbd765392121d27b790d7818e80dd28500b554db16e9186b1025ba9 libdatadog-aarch64-alpine-linux-musl.tar.gz"
        "2d7933e09dc39706e9c99c7edcff5c60f7567ea2777157596de828f62f39035b libdatadog-aarch64-apple-darwin.tar.gz"
        "decc01a2e0f732cabcc56594429a3dbc13678070e07f24891555dcc02df2e516 libdatadog-aarch64-unknown-linux-gnu.tar.gz"
        "fdf4e188d0e92150ad2fbb22e65a645d86d8a4eb04bbd9754683ae1adaf48eb4 libdatadog-i686-alpine-linux-musl.tar.gz"
        "63ace200493cd8e108be11cbf5ba19b5bd9a2e1cb730bdefd0a14ae217b716f5 libdatadog-i686-unknown-linux-gnu.tar.gz"
        "8e09afd3cfb5ace85501f37b4bd6378299ebbf71189ccc2173169998b75b4b56 libdatadog-x86_64-alpine-linux-musl.tar.gz"
        "ced5db61e0ca8e974b9d59b0b6833c28e19445a3e4ec3c548fda965806c17560 libdatadog-x86_64-apple-darwin.tar.gz"
        "caaec84fc9afbcb3ec4618791b3c3f1ead65196009e9f07fd382e863dc3bdc66 libdatadog-x86_64-unknown-linux-gnu.tar.gz"
        "3ec847560bd1de86935c230f34cb58d2a0f3f17865349d094e18d3e8edf8518955ede05bf595dc3ca41f99a911760f891bcf58d92fc5c06ce6ad98cdc8f034e3 libdatadog-x64-windows.zip"
        "2d884d76a35e4c37d05f6902c16b9e08ce1565976a6c9cf5fbd30273d8bd5385ad2523658eebadb02f90543191e217b028e86722ae7bcc08cbca2c342a5b5e79 libdatadog-x86-windows.zip"
    )
endif()

# Determine platform-specific tarball name in a way that conforms to the libdatadog naming scheme in Github releases
if(CMAKE_SYSTEM_PROCESSOR MATCHES "aarch64|arm64")
    set(DD_ARCH "aarch64")
elseif(CMAKE_SYSTEM_PROCESSOR MATCHES "x86_64|amd64|AMD64")
    set(DD_ARCH "x86_64")
else()
    message(FATAL_ERROR "Unsupported architecture: ${CMAKE_SYSTEM_PROCESSOR}")
endif()

set(DD_EXT "tar.gz")
set(DD_HASH_ALGO "SHA256")

if(APPLE)
    set(DD_PLATFORM "apple-darwin")
elseif(UNIX)
    execute_process(
        COMMAND ldd --version
        OUTPUT_VARIABLE LDD_OUTPUT
        ERROR_VARIABLE LDD_OUTPUT
        OUTPUT_STRIP_TRAILING_WHITESPACE)

    if(LDD_OUTPUT MATCHES "musl")
        set(DD_PLATFORM "alpine-linux-musl")
    else()
        set(DD_PLATFORM "unknown-linux-gnu")
    endif()
elseif(WIN32)
    # WIN32 is True when it's Windows, including Win64
    set(DD_PLATFORM "windows")

    if(CMAKE_SIZEOF_VOID_P EQUAL 8)
        set(DD_ARCH "x64")
    else()
        set(DD_ARCH "x86")
    endif()

    set(DD_EXT "zip")
    set(DD_HASH_ALGO "SHA512")
else()
    message(FATAL_ERROR "Unsupported operating system")
endif()

set(DD_TARBALL "libdatadog-${DD_ARCH}-${DD_PLATFORM}.${DD_EXT}")

# Make sure we can get the checksum for the tarball
foreach(ENTRY IN LISTS DD_CHECKSUMS)
    if(ENTRY MATCHES "^([a-fA-F0-9]+) ${DD_TARBALL}$")
        set(DD_HASH "${CMAKE_MATCH_1}")
        break()
    endif()
endforeach()

if(NOT DEFINED DD_HASH)
    message(FATAL_ERROR "Could not find checksum for ${DD_TARBALL}")
endif()

# Clean up any existing downloads if they exist
set(TARBALL_PATH "${FETCHCONTENT_DOWNLOADS_DIR}/${DD_TARBALL}")

set(LIBDATADOG_URL "https://github.com/DataDog/libdatadog/releases/download/${TAG_LIBDATADOG}/${DD_TARBALL}")

if(EXISTS "${TARBALL_PATH}")
    file(${DD_HASH_ALGO} "${TARBALL_PATH}" EXISTING_HASH)

    if(NOT EXISTING_HASH STREQUAL DD_HASH)
        file(REMOVE "${TARBALL_PATH}")

        # Also remove the subbuild directory to force a fresh download
        file(REMOVE_RECURSE "${CMAKE_CURRENT_BINARY_DIR}/_deps/libdatadog-subbuild")
    endif()
endif()

# Use FetchContent to download and extract the library
FetchContent_Declare(
    libdatadog
    URL ${LIBDATADOG_URL}
    URL_HASH ${DD_HASH_ALGO}=${DD_HASH}
    DOWNLOAD_DIR "${FETCHCONTENT_DOWNLOADS_DIR}" SOURCE_DIR "${FETCHCONTENT_BASE_DIR}/libdatadog-src")

# Make the content available
FetchContent_MakeAvailable(libdatadog)

# Set up paths
get_filename_component(Datadog_ROOT "${libdatadog_SOURCE_DIR}" ABSOLUTE)
set(ENV{Datadog_ROOT} "${Datadog_ROOT}")
set(Datadog_DIR "${Datadog_ROOT}/cmake")

# Configure library preferences (static over shared)
if(NOT WIN32)
    set(CMAKE_FIND_LIBRARY_SUFFIXES_BACKUP ${CMAKE_FIND_LIBRARY_SUFFIXES})
    set(CMAKE_FIND_LIBRARY_SUFFIXES .a)
endif()

# Find the package
find_package(Datadog REQUIRED)

if(NOT WIN32)
    # Restore library preferences
    set(CMAKE_FIND_LIBRARY_SUFFIXES ${CMAKE_FIND_LIBRARY_SUFFIXES_BACKUP})
endif()
