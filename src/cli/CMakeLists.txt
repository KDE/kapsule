# SPDX-FileCopyrightText: 2024-2026 KDE Community
# SPDX-License-Identifier: BSD-3-Clause

# kapsule CLI - C++ command-line interface

add_executable(kapsule
    main.cpp
    output.cpp
)

target_include_directories(kapsule
    PRIVATE
        ${CMAKE_CURRENT_SOURCE_DIR}
)

target_link_libraries(kapsule
    PRIVATE
        Kapsule::KapsuleQt
        Qt6::Core
        QCoro6::Core
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
