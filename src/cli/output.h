/*
    SPDX-FileCopyrightText: 2024-2026 KDE Community
    SPDX-License-Identifier: GPL-3.0-or-later
*/

#ifndef KAPSULE_CLI_OUTPUT_H
#define KAPSULE_CLI_OUTPUT_H

#include <Kapsule/Types>

#include <iostream>
#include <string>
#include <string_view>

namespace Kapsule {

/**
 * @brief Console output helpers with scoped indentation.
 *
 * Provides styled terminal output with automatic indentation management.
 * Access via the out() function to get the singleton instance.
 *
 * Usage:
 * @code
 *     auto &o = out();
 *     o.section("Starting process...");
 *     {
 *         IndentGuard guard(o);
 *         o.info("Step 1");
 *         {
 *             IndentGuard guard2(o);
 *             o.success("Done");
 *         }
 *     }
 * @endcode
 */
class Output
{
public:
    Output(const Output &) = delete;
    Output &operator=(const Output &) = delete;

    /**
     * @brief Print an error message in red.
     */
    void error(std::string_view msg);

    /**
     * @brief Print a warning message in yellow.
     */
    void warning(std::string_view msg);

    /**
     * @brief Print a hint message in yellow.
     */
    void hint(std::string_view msg);

    /**
     * @brief Print a success message with green checkmark.
     */
    void success(std::string_view msg);

    /**
     * @brief Print a failure message with red X.
     */
    void failure(std::string_view msg);

    /**
     * @brief Print a bold section header.
     */
    void section(std::string_view title);

    /**
     * @brief Print a dimmed message.
     */
    void dim(std::string_view msg);

    /**
     * @brief Print an info message (no special formatting).
     */
    void info(std::string_view msg);

    /**
     * @brief Print a message based on MessageType.
     *
     * Used for daemon operation progress messages.
     */
    void print(MessageType type, std::string_view msg, int extraIndent = 0);

    /**
     * @brief Print a progress indicator.
     * @param description What's being tracked
     * @param current Current progress value
     * @param total Total value (-1 for indeterminate)
     */
    void progress(std::string_view description, int current, int total = -1);

    /**
     * @brief Complete a progress bar, clearing the line.
     * @param message Optional message to display after clearing (e.g., success/failure)
     */
    void progressComplete(std::string_view message = "");

    /**
     * @brief Increase indentation level.
     */
    void indent(int spaces = 2);

    /**
     * @brief Decrease indentation level.
     */
    void dedent(int spaces = 2);

    /**
     * @brief Get current indentation level.
     */
    [[nodiscard]] int indentLevel() const { return m_indentLevel; }

private:
    friend Output &out();
    Output() = default;

    void printPrefix(int extraIndent = 0);

    std::ostream &m_stream = std::cerr;
    int m_indentLevel = 0;
};

/**
 * @brief Get the global Output instance.
 */
Output &out();

/**
 * @brief RAII guard for scoped indentation.
 *
 * Usage:
 * @code
 *     auto &o = out();
 *     o.info("Level 0");
 *     {
 *         IndentGuard guard(o);
 *         o.info("Level 1");
 *     }
 *     o.info("Back to level 0");
 * @endcode
 */
class IndentGuard
{
public:
    explicit IndentGuard(Output &output, int spaces = 2)
        : m_output(output)
        , m_spaces(spaces)
    {
        m_output.indent(m_spaces);
    }

    ~IndentGuard()
    {
        m_output.dedent(m_spaces);
    }

    IndentGuard(const IndentGuard &) = delete;
    IndentGuard &operator=(const IndentGuard &) = delete;

private:
    Output &m_output;
    int m_spaces;
};

/**
 * @brief RAII guard for operation blocks (prints header + indents).
 *
 * Usage:
 * @code
 *     auto &o = out();
 *     {
 *         OperationGuard op(o, "Creating container...");
 *         o.info("Step 1");
 *         o.success("Done");
 *     }
 * @endcode
 */
class OperationGuard
{
public:
    explicit OperationGuard(Output &output, std::string_view title, int spaces = 2)
        : m_output(output)
        , m_spaces(spaces)
    {
        m_output.section(title);
        m_output.indent(m_spaces);
    }

    ~OperationGuard()
    {
        m_output.dedent(m_spaces);
    }

    OperationGuard(const OperationGuard &) = delete;
    OperationGuard &operator=(const OperationGuard &) = delete;

private:
    Output &m_output;
    int m_spaces;
};

} // namespace Kapsule

#endif // KAPSULE_CLI_OUTPUT_H
