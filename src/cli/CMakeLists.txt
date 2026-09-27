# SPDX-FileCopyrightText: 2024-2026 KDE Community
# SPDX-License-Identifier: BSD-3-Clause

# kapsule CLI - C++ command-line interface

add_executable(kapsule
    main.cpp
    output.cpp
)

# QCoro's waitFor() uses exceptions internally, so we need to enable them
kde_target_enable_exceptions(kapsule PRIVATE)

target_include_directories(kapsule
    PRIVATE
        ${CMAKE_CURRENT_SOURCE_DIR}
)

target_link_libraries(kapsule
    PRIVATE
        Kapsule::KapsuleQt
        Qt6::Core
        Qt6::DBus
        QCoro6::Core
)

target_compile_definitions(kapsule
    PRIVATE KAPSULE_INCUS_CONFIG_DIR="${KDE_INSTALL_FULL_DATADIR}/kapsule/incus"
)

# Install kapsule executable
install(TARGETS kapsule
    DESTINATION ${KDE_INSTALL_BINDIR}
)

# Create 'kap' symlink to kapsule
install(CODE "
    execute_process(
        COMMAND ${CMAKE_COMMAND} -E create_symlink
            kapsule
            \"\$ENV{DESTDIR}\${CMAKE_INSTALL_PREFIX}/${KDE_INSTALL_BINDIR}/kap\"
    )
    message(STATUS \"Created symlink: kap -> kapsule\")
")
