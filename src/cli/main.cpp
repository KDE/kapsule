/*
    SPDX-FileCopyrightText: 2024-2026 KDE Community
    SPDX-License-Identifier: GPL-3.0-or-later
*/

#include "output.h"
#include "rang.hpp"

#include <Kapsule/KapsuleClient>
#include <Kapsule/Container>
#include <Kapsule/Types>

#include <QCommandLineParser>
#include <QCoreApplication>
#include <QFileInfo>

#include <qcoro/qcorotask.h>
#include <qcoro/qcorocore.h>

#include <unistd.h>
#include <cerrno>
#include <cstring>
#include <iomanip>
#include <iostream>

using namespace Kapsule;

// Program name (kap or kapsule) - set at startup
static QString programName;

// Forward declarations for command handlers
QCoro::Task<int> cmdCreate(KapsuleClient &client, const QStringList &args);
QCoro::Task<int> cmdEnter(KapsuleClient &client, const QStringList &args);
QCoro::Task<int> cmdList(KapsuleClient &client, const QStringList &args);
QCoro::Task<int> cmdStart(KapsuleClient &client, const QStringList &args);
QCoro::Task<int> cmdStop(KapsuleClient &client, const QStringList &args);
QCoro::Task<int> cmdRm(KapsuleClient &client, const QStringList &args);
QCoro::Task<int> cmdConfig(KapsuleClient &client, const QStringList &args);

void printUsage()
{
    auto &o = out();
    o.info(QStringLiteral("Usage: %1 <command> [options]").arg(programName).toStdString());
    o.info("");
    o.section("Commands:");
    {
        IndentGuard g(o);
        o.info("create <name>    Create a new container");
        o.info("enter [name]     Enter a container (default if configured)");;
        o.info("list             List containers");
        o.info("start <name>     Start a stopped container");
        o.info("stop <name>      Stop a running container");
        o.info("rm <name>        Remove a container");
        o.info("config           Show configuration");
    }
    o.info("");
    o.dim(QStringLiteral("Run '%1 <command> --help' for command-specific help.").arg(programName).toStdString());
}

QCoro::Task<int> asyncMain(const QStringList &args)
{
    auto &o = out();

    if (args.size() < 2) {
        printUsage();
        co_return 0;
    }

    QString command = args.at(1);

    // Handle --help and --version at top level
    if (command == QStringLiteral("--help") || command == QStringLiteral("-h")) {
        printUsage();
        co_return 0;
    }

    if (command == QStringLiteral("--version") || command == QStringLiteral("-V")) {
        o.info(QStringLiteral("%1 version %2").arg(programName, QCoreApplication::applicationVersion()).toStdString());
        co_return 0;
    }

    // Create client and check connection
    KapsuleClient client;

    if (!client.isConnected()) {
        o.error("Cannot connect to kapsule-daemon");
        o.hint("Is the daemon running? Try: systemctl status kapsule-daemon");
        co_return 1;
    }

    // Remaining args after command
    QStringList cmdArgs = args.mid(2);

    // Dispatch to command handlers
    if (command == QStringLiteral("create")) {
        co_return co_await cmdCreate(client, cmdArgs);
    } else if (command == QStringLiteral("enter")) {
        co_return co_await cmdEnter(client, cmdArgs);
    } else if (command == QStringLiteral("list") || command == QStringLiteral("ls")) {
        co_return co_await cmdList(client, cmdArgs);
    } else if (command == QStringLiteral("start")) {
        co_return co_await cmdStart(client, cmdArgs);
    } else if (command == QStringLiteral("stop")) {
        co_return co_await cmdStop(client, cmdArgs);
    } else if (command == QStringLiteral("rm") || command == QStringLiteral("remove")) {
        co_return co_await cmdRm(client, cmdArgs);
    } else if (command == QStringLiteral("config")) {
        co_return co_await cmdConfig(client, cmdArgs);
    } else {
        o.error(QStringLiteral("Unknown command: %1").arg(command).toStdString());
        printUsage();
        co_return 1;
    }
}

// =============================================================================
// Command: create
// =============================================================================

/**
 * Build QCommandLineOption entries and a variant-map builder from the schema.
 *
 * For each schema option the mapping is:
 *   boolean → --<flag> and --no-<flag>  (both registered, default marked)
 *   string  → --<flag> <value>
 *   array   → --<flag> <value>           (repeatable)
 *
 * Returns: list of options to add to the parser.
 */
static QList<QCommandLineOption> schemaToCliOptions(const CreateSchema &schema)
{
    QList<QCommandLineOption> cliOptions;
    for (const auto &opt : schema.allOptions()) {
        const QString flag = opt.cliFlag();

        if (opt.type == QStringLiteral("boolean")) {
            // Register both --<flag> and --no-<flag> so the user can
            // explicitly enable or disable any boolean option.
            if (opt.defaultsToTrue()) {
                cliOptions.append({
                    flag,
                    opt.description + QStringLiteral(" [default]")
                });
                cliOptions.append({
                    QStringLiteral("no-") + flag,
                    QStringLiteral("Disable: ") + opt.title
                });
            } else {
                cliOptions.append({
                    flag,
                    opt.description
                });
                cliOptions.append({
                    QStringLiteral("no-") + flag,
                    QStringLiteral("Disable: ") + opt.title + QStringLiteral(" [default]")
                });
            }
        } else if (opt.type == QStringLiteral("string")) {
            cliOptions.append({
                flag,
                opt.description,
                opt.title  // value name shown in help
            });
        } else if (opt.type == QStringLiteral("array")) {
            cliOptions.append({
                flag,
                opt.description + QStringLiteral(" (repeatable)"),
                opt.title
            });
        }
    }
    return cliOptions;
}

/**
 * After parsing, walk the schema and build a QVariantMap containing only
 * the options the user explicitly set (non-default values).  The daemon
 * fills in defaults for anything omitted.
 */
static QVariantMap cliToVariantMap(const QCommandLineParser &parser,
                                   const CreateSchema &schema)
{
    QVariantMap map;
    for (const auto &opt : schema.allOptions()) {
        const QString flag = opt.cliFlag();

        if (opt.type == QStringLiteral("boolean")) {
            const bool hasPositive = parser.isSet(flag);
            const bool hasNegative = parser.isSet(QStringLiteral("no-") + flag);

            // Last-one-wins if the user passes both (unlikely but harmless).
            // Only insert when explicitly set so the daemon uses its defaults
            // for anything omitted.
            if (hasPositive && !hasNegative) {
                map.insert(opt.key, true);
            } else if (hasNegative && !hasPositive) {
                map.insert(opt.key, false);
            }
        } else if (opt.type == QStringLiteral("string")) {
            if (parser.isSet(flag)) {
                map.insert(opt.key, parser.value(flag));
            }
        } else if (opt.type == QStringLiteral("array")) {
            QStringList values = parser.values(flag);
            if (!values.isEmpty()) {
                map.insert(opt.key, values);
            }
        }
    }
    return map;
}

QCoro::Task<int> cmdCreate(KapsuleClient &client, const QStringList &args)
{
    auto &o = out();

    // ---- Fetch the option schema from the daemon ----
    QString schemaJson = co_await client.getCreateSchema();
    if (schemaJson.isEmpty()) {
        o.error("Failed to retrieve create-container schema from daemon");
        co_return 1;
    }
    CreateSchema schema = parseCreateSchema(schemaJson);
    if (schema.version == 0) {
        o.error("Failed to parse create-container schema");
        co_return 1;
    }

    // ---- Build parser dynamically ----
    QCommandLineParser parser;
    parser.setApplicationDescription(QStringLiteral("Create a new kapsule container"));
    parser.addHelpOption();
    parser.addPositionalArgument(QStringLiteral("name"), QStringLiteral("Name of the container to create"));

    // The --image flag is not part of the schema (it is a separate D-Bus parameter)
    parser.addOption({{QStringLiteral("i"), QStringLiteral("image")},
                      QStringLiteral("Base image to use (e.g., images:ubuntu/24.04)"),
                      QStringLiteral("image")});

    // Add schema-driven flags
    const auto schemaOptions = schemaToCliOptions(schema);
    for (const auto &cliOpt : schemaOptions) {
        parser.addOption(cliOpt);
    }

    // ---- Parse ----
    QStringList fullArgs = QStringList{programName + QStringLiteral(" create")} + args;
    if (!parser.parse(fullArgs)) {
        o.error(parser.errorText().toStdString());
        co_return 1;
    }

    if (parser.isSet(QStringLiteral("help"))) {
        std::cout << parser.helpText().toStdString();
        co_return 0;
    }

    QStringList positional = parser.positionalArguments();
    if (positional.isEmpty()) {
        o.error("Container name required");
        o.hint(QStringLiteral("Usage: %1 create <name> [--image <image>]").arg(programName).toStdString());
        co_return 1;
    }

    QString name = positional.at(0);
    QString image = parser.value(QStringLiteral("image"));

    // Build variant map from user-specified flags only
    QVariantMap optionsMap = cliToVariantMap(parser, schema);

    o.section(QStringLiteral("Creating container: %1").arg(name).toStdString());

    auto result = co_await client.createContainer(name, image, optionsMap,
        [&o](MessageType type, const QString &msg, int indent) {
            o.print(type, msg.toStdString(), indent);
        });

    if (!result.success) {
        o.failure(result.error.toStdString());
        co_return 1;
    }

    o.success("Container created");
    co_return 0;
}

// =============================================================================
// Command: enter
// =============================================================================

QCoro::Task<int> cmdEnter(KapsuleClient &client, const QStringList &args)
{
    auto &o = out();

    QCommandLineParser parser;
    parser.setApplicationDescription(QStringLiteral("Enter a kapsule container"));
    parser.addHelpOption();
    parser.addPositionalArgument(QStringLiteral("name"), QStringLiteral("Container name (optional, uses default)"));
    parser.addPositionalArgument(QStringLiteral("command"), QStringLiteral("Command to run (optional)"));

    QStringList fullArgs = QStringList{programName + QStringLiteral(" enter")} + args;
    if (!parser.parse(fullArgs)) {
        o.error(parser.errorText().toStdString());
        co_return 1;
    }

    if (parser.isSet(QStringLiteral("help"))) {
        std::cout << parser.helpText().toStdString();
        co_return 0;
    }

    QStringList positional = parser.positionalArguments();

    // Handle "--" separator for commands
    QString containerName;
    QStringList command;

    int dashIdx = args.indexOf(QStringLiteral("--"));
    if (dashIdx >= 0) {
        // Everything before -- could be container name
        if (dashIdx > 0) {
            containerName = args.at(0);
        }
        // Everything after -- is the command
        command = args.mid(dashIdx + 1);
    } else if (!positional.isEmpty()) {
        containerName = positional.at(0);
        command = positional.mid(1);
    }

    auto result = co_await client.prepareEnter(containerName, command);

    if (!result.success) {
        o.error(result.error.toStdString());
        co_return 1;
    }

    // Execute the command (replaces current process)
    QByteArrayList execArgsBytes;
    for (const QString &arg : result.execArgs) {
        execArgsBytes.append(arg.toLocal8Bit());
    }

    std::vector<char *> execArgv;
    for (QByteArray &arg : execArgsBytes) {
        execArgv.push_back(arg.data());
    }
    execArgv.push_back(nullptr);

    execvp(execArgv[0], execArgv.data());

    // If we get here, exec failed
    o.error(QStringLiteral("Failed to exec: %1").arg(QString::fromLocal8Bit(strerror(errno))).toStdString());
    co_return 1;
}

// =============================================================================
// Command: list
// =============================================================================

QCoro::Task<int> cmdList(KapsuleClient &client, const QStringList &args)
{
    auto &o = out();

    QCommandLineParser parser;
    parser.setApplicationDescription(QStringLiteral("List kapsule containers"));
    parser.addHelpOption();
    parser.addOptions({
        {{QStringLiteral("a"), QStringLiteral("all")},
         QStringLiteral("Show all containers including stopped")},
    });

    QStringList fullArgs = QStringList{programName + QStringLiteral(" list")} + args;
    if (!parser.parse(fullArgs)) {
        o.error(parser.errorText().toStdString());
        co_return 1;
    }

    if (parser.isSet(QStringLiteral("help"))) {
        std::cout << parser.helpText().toStdString();
        co_return 0;
    }

    bool showAll = parser.isSet(QStringLiteral("all"));

    auto containers = co_await client.listContainers();

    if (containers.isEmpty()) {
        o.dim("No containers found.");
        co_return 0;
    }

    // Filter if not --all
    if (!showAll) {
        containers.erase(
            std::remove_if(containers.begin(), containers.end(),
                [](const Container &c) { return c.state() != Container::State::Running; }),
            containers.end());

        if (containers.isEmpty()) {
            o.dim("No running containers. Use --all to see stopped containers.");
            co_return 0;
        }
    }

    // Print table header
    std::cout << rang::style::bold
              << std::left << std::setw(20) << "NAME"
              << std::setw(12) << "STATUS"
              << std::setw(25) << "IMAGE"
              << std::setw(12) << "MODE"
              << "CREATED"
              << rang::style::reset << '\n';

    // Print rows
    for (const Container &c : containers) {
        std::string status;
        switch (c.state()) {
        case Container::State::Running:
            std::cout << rang::fg::green;
            status = "Running";
            break;
        case Container::State::Stopped:
            std::cout << rang::fg::red;
            status = "Stopped";
            break;
        case Container::State::Starting:
            std::cout << rang::fg::yellow;
            status = "Starting";
            break;
        case Container::State::Stopping:
            std::cout << rang::fg::yellow;
            status = "Stopping";
            break;
        default:
            std::cout << rang::fg::gray;
            status = "Unknown";
        }

        std::cout << std::left << std::setw(20) << c.name().toStdString()
                  << std::setw(12) << status
                  << rang::fg::reset
                  << std::setw(25) << c.image().toStdString()
                  << std::setw(12) << containerModeToString(c.mode()).toStdString()
                  << c.created().toString(Qt::ISODate).left(10).toStdString()
                  << '\n';
    }

    co_return 0;
}

// =============================================================================
// Command: start
// =============================================================================

QCoro::Task<int> cmdStart(KapsuleClient &client, const QStringList &args)
{
    auto &o = out();

    QCommandLineParser parser;
    parser.setApplicationDescription(QStringLiteral("Start a stopped container"));
    parser.addHelpOption();
    parser.addPositionalArgument(QStringLiteral("name"), QStringLiteral("Container name"));

    QStringList fullArgs = QStringList{programName + QStringLiteral(" start")} + args;
    if (!parser.parse(fullArgs)) {
        o.error(parser.errorText().toStdString());
        co_return 1;
    }

    if (parser.isSet(QStringLiteral("help"))) {
        std::cout << parser.helpText().toStdString();
        co_return 0;
    }

    QStringList positional = parser.positionalArguments();
    if (positional.isEmpty()) {
        o.error("Container name required");
        co_return 1;
    }

    QString name = positional.at(0);

    o.section(QStringLiteral("Starting container: %1").arg(name).toStdString());

    auto result = co_await client.startContainer(name,
        [&o](MessageType type, const QString &msg, int indent) {
            o.print(type, msg.toStdString(), indent);
        });

    if (!result.success) {
        o.failure(result.error.toStdString());
        co_return 1;
    }

    o.success("Container started");
    co_return 0;
}

// =============================================================================
// Command: stop
// =============================================================================

QCoro::Task<int> cmdStop(KapsuleClient &client, const QStringList &args)
{
    auto &o = out();

    QCommandLineParser parser;
    parser.setApplicationDescription(QStringLiteral("Stop a running container"));
    parser.addHelpOption();
    parser.addPositionalArgument(QStringLiteral("name"), QStringLiteral("Container name"));
    parser.addOptions({
        {{QStringLiteral("f"), QStringLiteral("force")},
         QStringLiteral("Force stop the container")},
    });

    QStringList fullArgs = QStringList{programName + QStringLiteral(" stop")} + args;
    if (!parser.parse(fullArgs)) {
        o.error(parser.errorText().toStdString());
        co_return 1;
    }

    if (parser.isSet(QStringLiteral("help"))) {
        std::cout << parser.helpText().toStdString();
        co_return 0;
    }

    QStringList positional = parser.positionalArguments();
    if (positional.isEmpty()) {
        o.error("Container name required");
        co_return 1;
    }

    QString name = positional.at(0);
    bool force = parser.isSet(QStringLiteral("force"));

    o.section(QStringLiteral("Stopping container: %1").arg(name).toStdString());

    auto result = co_await client.stopContainer(name, force,
        [&o](MessageType type, const QString &msg, int indent) {
            o.print(type, msg.toStdString(), indent);
        });

    if (!result.success) {
        o.failure(result.error.toStdString());
        co_return 1;
    }

    o.success("Container stopped");
    co_return 0;
}

// =============================================================================
// Command: rm
// =============================================================================

QCoro::Task<int> cmdRm(KapsuleClient &client, const QStringList &args)
{
    auto &o = out();

    QCommandLineParser parser;
    parser.setApplicationDescription(QStringLiteral("Remove a container"));
    parser.addHelpOption();
    parser.addPositionalArgument(QStringLiteral("name"), QStringLiteral("Container name"));
    parser.addOptions({
        {{QStringLiteral("f"), QStringLiteral("force")},
         QStringLiteral("Force removal even if running")},
    });

    QStringList fullArgs = QStringList{programName + QStringLiteral(" rm")} + args;
    if (!parser.parse(fullArgs)) {
        o.error(parser.errorText().toStdString());
        co_return 1;
    }

    if (parser.isSet(QStringLiteral("help"))) {
        std::cout << parser.helpText().toStdString();
        co_return 0;
    }

    QStringList positional = parser.positionalArguments();
    if (positional.isEmpty()) {
        o.error("Container name required");
        co_return 1;
    }

    QString name = positional.at(0);
    bool force = parser.isSet(QStringLiteral("force"));

    o.section(QStringLiteral("Removing container: %1").arg(name).toStdString());

    auto result = co_await client.deleteContainer(name, force,
        [&o](MessageType type, const QString &msg, int indent) {
            o.print(type, msg.toStdString(), indent);
        });

    if (!result.success) {
        o.failure(result.error.toStdString());
        co_return 1;
    }

    o.success("Container removed");
    co_return 0;
}

// =============================================================================
// Command: config
// =============================================================================

QCoro::Task<int> cmdConfig(KapsuleClient &client, const QStringList &args)
{
    auto &o = out();

    QCommandLineParser parser;
    parser.setApplicationDescription(QStringLiteral("View kapsule configuration"));
    parser.addHelpOption();
    parser.addPositionalArgument(QStringLiteral("key"), QStringLiteral("Config key to display (optional)"));

    QStringList fullArgs = QStringList{programName + QStringLiteral(" config")} + args;
    if (!parser.parse(fullArgs)) {
        o.error(parser.errorText().toStdString());
        co_return 1;
    }

    if (parser.isSet(QStringLiteral("help"))) {
        std::cout << parser.helpText().toStdString();
        co_return 0;
    }

    QStringList positional = parser.positionalArguments();
    QString key = positional.value(0);

    auto config = co_await client.config();

    if (config.contains(QStringLiteral("error"))) {
        o.error(config.value(QStringLiteral("error")).toString().toStdString());
        co_return 1;
    }

    if (key.isEmpty()) {
        // Show all config
        o.section("Configuration");
        {
            IndentGuard g(o);
            o.info(QStringLiteral("default_container: %1")
                .arg(config.value(QStringLiteral("default_container")).toString())
                .toStdString());
            o.info(QStringLiteral("default_image: %1")
                .arg(config.value(QStringLiteral("default_image")).toString())
                .toStdString());
        }
    } else {
        // Show single key
        QStringList validKeys = {QStringLiteral("default_container"), QStringLiteral("default_image")};
        if (!validKeys.contains(key)) {
            o.error(QStringLiteral("Unknown config key: %1").arg(key).toStdString());
            o.hint(QStringLiteral("Valid keys: %1").arg(validKeys.join(QStringLiteral(", "))).toStdString());
            co_return 1;
        }
        o.info(QStringLiteral("%1 = %2").arg(key, config.value(key).toString()).toStdString());
    }

    co_return 0;
}

// =============================================================================
// Main entry point
// =============================================================================

int main(int argc, char *argv[])
{
    QCoreApplication app(argc, argv);
    
    // Detect program name from argv[0] (kap or kapsule)
    QString argv0 = QString::fromLocal8Bit(argv[0]);
    programName = QFileInfo(argv0).fileName();
    // Normalize to either "kap" or "kapsule"
    if (programName != QStringLiteral("kap")) {
        programName = QStringLiteral("kapsule");
    }
    
    app.setApplicationName(programName);
    app.setApplicationVersion(QStringLiteral("0.1.0"));  // TODO: Get from build
    app.setOrganizationDomain(QStringLiteral("kde.org"));
    app.setOrganizationName(QStringLiteral("KDE"));

    return QCoro::waitFor(asyncMain(app.arguments()));
}
