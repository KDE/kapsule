/*
    SPDX-FileCopyrightText: 2024-2026 KDE Community
    SPDX-License-Identifier: GPL-3.0-or-later
*/

#include "output.h"
#include "rang.hpp"

#include <algorithm>
#include <iomanip>

namespace Kapsule {

Output &out()
{
    static Output instance;
    return instance;
}

void Output::printPrefix(int extraIndent)
{
    int total = m_indentLevel + extraIndent;
    for (int i = 0; i < total; ++i) {
        m_stream << ' ';
    }
}

void Output::error(std::string_view msg)
{
    printPrefix();
    m_stream << rang::fg::red << "Error:" << rang::fg::reset << " " << msg << '\n';
}

void Output::warning(std::string_view msg)
{
    printPrefix();
    m_stream << rang::fg::yellow << "Warning:" << rang::fg::reset << " " << msg << '\n';
}

void Output::hint(std::string_view msg)
{
    printPrefix();
    m_stream << rang::fg::yellow << "Hint:" << rang::fg::reset << " " << msg << '\n';
}

void Output::success(std::string_view msg)
{
    printPrefix();
    m_stream << rang::fg::green << "✓" << rang::fg::reset << " " << msg << '\n';
}

void Output::failure(std::string_view msg)
{
    printPrefix();
    m_stream << rang::fg::red << "✗" << rang::fg::reset << " " << msg << '\n';
}

void Output::section(std::string_view title)
{
    printPrefix();
    m_stream << rang::style::bold << rang::fg::blue << title
             << rang::fg::reset << rang::style::reset << '\n';
}

void Output::dim(std::string_view msg)
{
    printPrefix();
    m_stream << rang::style::dim << msg << rang::style::reset << '\n';
}

void Output::info(std::string_view msg)
{
    printPrefix();
    m_stream << msg << '\n';
}

void Output::print(MessageType type, std::string_view msg, int extraIndent)
{
    // Calculate total indent (current level + extra from message)
    int savedIndent = m_indentLevel;
    m_indentLevel += extraIndent * 2;  // 2 spaces per indent level

    switch (type) {
    case MessageType::Info:
        info(msg);
        break;
    case MessageType::Success:
        success(msg);
        break;
    case MessageType::Warning:
        warning(msg);
        break;
    case MessageType::Error:
        error(msg);
        break;
    case MessageType::Dim:
        dim(msg);
        break;
    case MessageType::Hint:
        hint(msg);
        break;
    }

    m_indentLevel = savedIndent;
}

void Output::progress(std::string_view description, int current, int total)
{
    printPrefix();

    if (total > 0) {
        // Determinate progress
        int percent = (current * 100) / total;
        int barWidth = 30;
        int filled = (current * barWidth) / total;

        m_stream << rang::fg::cyan << description << rang::fg::reset << " [";
        for (int i = 0; i < barWidth; ++i) {
            if (i < filled) {
                m_stream << rang::fg::green << "█" << rang::fg::reset;
            } else {
                m_stream << rang::style::dim << "░" << rang::style::reset;
            }
        }
        m_stream << "] " << percent << "%\r";
    } else {
        // Indeterminate progress (spinner-like)
        static const char *spinChars = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏";
        // Each spinner char is 3 bytes in UTF-8
        int idx = (current % 10) * 3;
        m_stream << rang::fg::cyan << std::string_view(spinChars + idx, 3)
                 << " " << description << rang::fg::reset << "\r";
    }
    m_stream.flush();
}

void Output::progressComplete(std::string_view message)
{
    // Clear the progress line (overwrite with spaces, then carriage return)
    m_stream << "\r" << std::string(80, ' ') << "\r";
    if (!message.empty()) {
        printPrefix();
        m_stream << message << '\n';
    }
    m_stream.flush();
}

void Output::indent(int spaces)
{
    m_indentLevel += spaces;
}

void Output::dedent(int spaces)
{
    m_indentLevel = std::max(0, m_indentLevel - spaces);
}

} // namespace Kapsule
