#include <iostream>
#include "../src/graph.hh"
#include "../src/io.hh"
#include "../src/global.hh"

using namespace std;

void print_help(const string &program_name) {
    cerr << "Usage: " << endl;
    cerr << "    " << program_name << " <packed_file>"
         << " <placement_file> <routing_graph_file> "
            "[routing_result.route]" << endl;
}

int main(int argc, char *argv[]) {
    if (argc < 4) {
        print_help(argv[0]);
        return EXIT_FAILURE;
    }
    string packed_filename = argv[1];
    string placement_filename = argv[2];
    string graph_filename = argv[3];
    auto [netlist, track_mode] = load_netlist(packed_filename);
    auto placement = load_placement(placement_filename);
    auto graph = load_routing_graph(graph_filename);

    // set up the router
    GlobalRouter r(100, graph);
    for (auto const &it : placement) {
        auto [x, y] = it.second;
        r.add_placement(x, y, it.first);
    }

    for (const auto &iter: netlist) {
        // Note
        // we only route 1bit at this time
        if (track_mode.at(iter.first) == 16)
            r.add_net(iter.first, iter.second);
    }

    r.route();

    if (argc > 4) {
        cout << "saving routing result to " << argv[4] << endl;
        dump_routing_result(r, argv[4]);
    }

    return EXIT_SUCCESS;
}